package com.uthelper.backgroundsync.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class NativeSettings(
    val enabled: Boolean = true,
    val intervalMinutes: Int = 60,
    val fetchMonths: Int = 1,
    val notifyTypes: Set<String> = setOf("quiz", "assignment", "attendance"),
    val mutedCourses: Set<String> = emptySet(),
    val ignoreSubmitted: Boolean = true,
    val countdownMinutes: List<Int> = listOf(4320, 1440, 180, 60, 30, 5),
    val dndEnabled: Boolean = false,
    val dndStartHour: Int = 22,
    val dndEndHour: Int = 7,
)

class SettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences(
        "uth_background_settings",
        Context.MODE_PRIVATE,
    )

    fun update(value: Map<*, *>) {
        val json = JSONObject()
        value.forEach { (key, item) ->
            if (key != null) json.put(key.toString(), JSONObject.wrap(item))
        }
        preferences.edit().putString(KEY, json.toString()).commit()
    }

    fun read(): NativeSettings {
        val json = runCatching {
            JSONObject(preferences.getString(KEY, "{}") ?: "{}")
        }.getOrDefault(JSONObject())
        val countdown = json.intList("countdown_minutes").ifEmpty {
            val hours = json.intList("notify_milestones_hours").map { it * 60 }
            val minute = json.optInt("notify_minutes_before", 0)
            (hours + listOfNotNull(minute.takeIf { it > 0 })).distinct()
        }.ifEmpty { NativeSettings().countdownMinutes }
        return NativeSettings(
            enabled = json.optBoolean("enabled", true),
            intervalMinutes = json.optInt("interval_minutes", 60)
                .takeIf { it in setOf(60, 180, 360) } ?: 60,
            fetchMonths = json.optInt("fetch_months", 1).coerceIn(1, 3),
            notifyTypes = json.stringSet("notify_types", NativeSettings().notifyTypes),
            mutedCourses = json.stringSet("muted_courses", emptySet()),
            ignoreSubmitted = json.optBoolean("ignore_submitted", true),
            countdownMinutes = countdown.filter { it > 0 }.distinct().sortedDescending(),
            dndEnabled = json.optBoolean("dnd_enabled", false),
            dndStartHour = json.optInt("dnd_start", 22).coerceIn(0, 23),
            dndEndHour = json.optInt("dnd_end", 7).coerceIn(0, 23),
        )
    }

    fun clear() = preferences.edit().clear().commit()

    private fun JSONObject.intList(key: String): List<Int> {
        val array = optJSONArray(key) ?: return emptyList()
        return (0 until array.length()).mapNotNull { array.optInt(it).takeIf { v -> v > 0 } }
    }

    private fun JSONObject.stringSet(key: String, fallback: Set<String>): Set<String> {
        val array: JSONArray = optJSONArray(key) ?: return fallback
        return (0 until array.length()).mapNotNull {
            array.optString(it).trim().lowercase().takeIf(String::isNotEmpty)
        }.toSet()
    }

    companion object { private const val KEY = "settings_json" }
}
