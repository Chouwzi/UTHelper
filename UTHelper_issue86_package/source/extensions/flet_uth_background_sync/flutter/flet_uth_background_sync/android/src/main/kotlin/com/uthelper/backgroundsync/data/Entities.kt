package com.uthelper.backgroundsync.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(tableName = "activities", indices = [Index("deadlineEpochSeconds")])
data class ActivityEntity(
    @PrimaryKey val activityKey: String,
    val activityId: String,
    val courseId: String,
    val courseName: String,
    val title: String,
    val eventType: String,
    val deadlineEpochSeconds: Long,
    val url: String,
    val submissionState: String,
    val graded: Boolean,
    val revision: String,
    val lastSeenGeneration: Long,
)

@Entity(tableName = "reminders", indices = [Index("activityKey")])
data class ReminderEntity(
    @PrimaryKey val notificationId: Int,
    val activityKey: String,
    val milestoneMinutes: Int,
    val revision: String,
    val scheduledAtEpochSeconds: Long,
)

@Entity(tableName = "delivered_milestones")
data class DeliveredMilestoneEntity(
    @PrimaryKey val deliveryKey: String,
    val activityKey: String,
    val revision: String,
    val milestoneMinutes: Int,
    val deliveredAtEpochSeconds: Long,
)

@Entity(tableName = "sync_state")
data class SyncStateEntity(
    @PrimaryKey val key: String,
    val value: String,
)
