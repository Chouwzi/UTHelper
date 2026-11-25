package com.uthelper.backgroundsync.notification

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject

/** Persists and schedules local reminders for upcoming online and in-person Portal classes. */
class ClassScheduleAlarmScheduler(private val context: Context) {
    private val alarmManager = context.getSystemService(AlarmManager::class.java)
    private val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun reconcile(values: List<*>, enabled: Boolean): Map<String, Int> {
        return synchronized(PREFS_LOCK) {
            val previous = readItems()
            previous.forEach(::cancel)

            val now = System.currentTimeMillis() / 1000L
            val desired = if (enabled) {
                values.mapNotNull(::parseItem)
                    .filter { it.optLong(KEY_SCHEDULED_AT) > now && it.optLong(KEY_START_AT) > now }
                    .distinctBy { it.optString(KEY_REMINDER_ID) }
                    .sortedBy { it.optLong(KEY_SCHEDULED_AT) }
            } else {
                // Full teardown (logout / credentials change): banners already
                // sitting on the notification tray are exactly as stale as the
                // alarms just cancelled above, so drop them too.
                previous.forEach { dropPostedNotification(it.optString(KEY_REMINDER_ID)) }
                emptyList<JSONObject>()
            }

            preferences.edit().putString(KEY_ITEMS, JSONArray(desired).toString()).commit()
            desired.forEach(::schedule)
            mapOf(
                "desired" to desired.size,
                "scheduled" to desired.size,
                "cancelled" to previous.size,
            )
        }
    }

    fun reschedulePersisted() {
        synchronized(PREFS_LOCK) {
            val now = System.currentTimeMillis() / 1000L
            val current = readItems()
            val future = current.filter {
                it.optLong(KEY_SCHEDULED_AT) > now && it.optLong(KEY_START_AT) > now
            }
            val stale = current.size - future.size
            if (stale > 0) {
                preferences.edit().putString(KEY_ITEMS, JSONArray(future).toString()).commit()
            }
            future.forEach(::schedule)
        }
    }

    fun remove(reminderId: String) {
        if (reminderId.isBlank()) return
        synchronized(PREFS_LOCK) {
            val current = readItems()
            val removed = current.filter { it.optString(KEY_REMINDER_ID) == reminderId }
            removed.forEach(::cancel)
            val remaining = current.filterNot { it.optString(KEY_REMINDER_ID) == reminderId }
            preferences.edit().putString(KEY_ITEMS, JSONArray(remaining).toString()).commit()
        }
    }

    fun cancelAll() {
        synchronized(PREFS_LOCK) {
            readItems().forEach(::cancel)
            preferences.edit().remove(KEY_ITEMS).commit()
        }
    }

    fun contains(reminderId: String): Boolean {
        if (reminderId.isBlank()) return false
        return synchronized(PREFS_LOCK) {
            readItems().any { it.optString(KEY_REMINDER_ID) == reminderId }
        }
    }

    private fun parseItem(raw: Any?): JSONObject? {
        val map = raw as? Map<*, *> ?: return null
        val reminderId = map[KEY_REMINDER_ID]?.toString()?.trim().orEmpty()
        val subject = map["subject"]?.toString()?.trim().orEmpty()
        val room = map["room"]?.toString()?.trim().orEmpty()
        val campus = map["campus"]?.toString()?.trim().orEmpty()
        val deliveryMode = map["delivery_mode"]?.toString()?.trim()?.lowercase().orEmpty()
        val leadHours = (map["lead_hours"] as? Number)?.toInt() ?: return null
        val scheduledAt = (map[KEY_SCHEDULED_AT] as? Number)?.toLong() ?: return null
        val startAt = (map[KEY_START_AT] as? Number)?.toLong() ?: return null
        val locationIsValid = deliveryMode == "online" ||
            (deliveryMode == "in_person" && (room.isNotBlank() || campus.isNotBlank()))
        if (reminderId.isBlank() || subject.isBlank() || !locationIsValid) return null
        if (leadHours !in 1..3 || scheduledAt <= 0L || startAt <= scheduledAt) return null
        return JSONObject().apply {
            put(KEY_REMINDER_ID, reminderId)
            put("subject", subject)
            put("course_code", map["course_code"]?.toString().orEmpty())
            put("room", room)
            put("campus", campus)
            put("note", map["note"]?.toString().orEmpty())
            put("delivery_mode", deliveryMode)
            put("lead_hours", leadHours)
            put(KEY_SCHEDULED_AT, scheduledAt)
            put(KEY_START_AT, startAt)
        }
    }

    private fun readItems(): List<JSONObject> {
        val raw = preferences.getString(KEY_ITEMS, "[]") ?: "[]"
        val array = runCatching { JSONArray(raw) }.getOrDefault(JSONArray())
        return (0 until array.length()).mapNotNull { index ->
            array.optJSONObject(index)
        }
    }

    private fun schedule(item: JSONObject) {
        val operation = pendingIntent(item, PendingIntent.FLAG_UPDATE_CURRENT) ?: return
        val triggerAtMillis = item.optLong(KEY_SCHEDULED_AT) * 1000L
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !alarmManager.canScheduleExactAlarms()) {
            alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            try {
                alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
            } catch (_: SecurityException) {
                alarmManager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
            }
        } else {
            try {
                alarmManager.setExact(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
            } catch (_: SecurityException) {
                alarmManager.set(AlarmManager.RTC_WAKEUP, triggerAtMillis, operation)
            }
        }
    }

    private fun cancel(item: JSONObject) {
        val operation = pendingIntent(item, PendingIntent.FLAG_NO_CREATE) ?: return
        alarmManager.cancel(operation)
        operation.cancel()
    }

    /** Removes an already displayed class banner using the receiver's id. */
    private fun dropPostedNotification(reminderId: String) {
        if (reminderId.isBlank()) return
        runCatching {
            NotificationManagerCompat.from(context)
                .cancel(reminderId.hashCode() and 0x7fffffff)
        }
    }

    private fun pendingIntent(item: JSONObject, flag: Int): PendingIntent? {
        val reminderId = item.optString(KEY_REMINDER_ID)
        val requestCode = reminderId.hashCode() and 0x7fffffff
        val intent = Intent(context, ClassScheduleAlarmReceiver::class.java).apply {
            action = ACTION_CLASS_SCHEDULE
            data = Uri.parse("uthclass://$reminderId")
            putExtra(KEY_REMINDER_ID, reminderId)
            putExtra("subject", item.optString("subject"))
            putExtra("course_code", item.optString("course_code"))
            putExtra("room", item.optString("room"))
            putExtra("campus", item.optString("campus"))
            putExtra("note", item.optString("note"))
            putExtra("delivery_mode", item.optString("delivery_mode"))
            putExtra("lead_hours", item.optInt("lead_hours", 1))
            putExtra(KEY_START_AT, item.optLong(KEY_START_AT))
        }
        return PendingIntent.getBroadcast(
            context,
            requestCode,
            intent,
            flag or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    companion object {
        const val ACTION_CLASS_SCHEDULE = "com.uthelper.backgroundsync.CLASS_SCHEDULE"
        const val KEY_REMINDER_ID = "reminder_id"
        const val KEY_SCHEDULED_AT = "scheduled_at_epoch_seconds"
        const val KEY_START_AT = "start_at_epoch_seconds"
        private const val PREFERENCES = "uth_class_schedule_reminders"
        private const val KEY_ITEMS = "items"
        private val PREFS_LOCK = Any()
    }
}
