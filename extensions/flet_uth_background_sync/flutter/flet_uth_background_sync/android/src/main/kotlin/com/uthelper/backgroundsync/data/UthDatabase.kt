package com.uthelper.backgroundsync.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(
    entities = [
        ActivityEntity::class,
        ReminderEntity::class,
        DeliveredMilestoneEntity::class,
        SyncStateEntity::class,
    ],
    version = 1,
    exportSchema = false,
)
abstract class UthDatabase : RoomDatabase() {
    abstract fun dao(): UthDao

    companion object {
        @Volatile private var instance: UthDatabase? = null

        fun get(context: Context): UthDatabase = instance ?: synchronized(this) {
            instance ?: Room.databaseBuilder(
                context.applicationContext,
                UthDatabase::class.java,
                "uth_background_sync.db",
            ).build().also { instance = it }
        }
    }
}
