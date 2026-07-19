package com.uthelper.backgroundsync.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction

@Dao
interface UthDao {
    @Query("SELECT * FROM activities ORDER BY deadlineEpochSeconds")
    suspend fun activities(): List<ActivityEntity>

    @Query("SELECT * FROM activities WHERE activityKey = :key LIMIT 1")
    suspend fun activity(key: String): ActivityEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertActivities(values: List<ActivityEntity>)

    @Query("DELETE FROM activities WHERE lastSeenGeneration < :generation")
    suspend fun deleteActivitiesBefore(generation: Long)

    @Query("DELETE FROM activities")
    suspend fun clearActivities()

    @Query("SELECT * FROM reminders")
    suspend fun reminders(): List<ReminderEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertReminder(value: ReminderEntity)

    @Query("DELETE FROM reminders WHERE notificationId = :notificationId")
    suspend fun deleteReminder(notificationId: Int)

    @Query("DELETE FROM reminders")
    suspend fun clearReminders()

    @Query("SELECT EXISTS(SELECT 1 FROM delivered_milestones WHERE deliveryKey = :key)")
    suspend fun wasDelivered(key: String): Boolean

    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun markDelivered(value: DeliveredMilestoneEntity)

    @Query("DELETE FROM delivered_milestones WHERE deliveredAtEpochSeconds < :cutoff")
    suspend fun pruneDelivered(cutoff: Long)

    @Query("SELECT value FROM sync_state WHERE key = :key LIMIT 1")
    suspend fun state(key: String): String?

    @Query("SELECT * FROM sync_state")
    suspend fun states(): List<SyncStateEntity>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun setState(value: SyncStateEntity)

    @Query("DELETE FROM sync_state")
    suspend fun clearState()

    @Transaction
    suspend fun replaceAuthoritativeSnapshot(
        values: List<ActivityEntity>,
        generation: Long,
    ) {
        if (values.isNotEmpty()) upsertActivities(values)
        deleteActivitiesBefore(generation)
        setState(SyncStateEntity("snapshot_generation", generation.toString()))
        setState(SyncStateEntity("activity_count", values.size.toString()))
    }
}
