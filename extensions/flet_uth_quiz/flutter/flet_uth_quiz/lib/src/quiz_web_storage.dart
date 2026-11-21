import 'package:flutter/foundation.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';

Future<void> purgeQuizWebStorage(
  TargetPlatform platform,
  WebStorageManager manager,
) async {
  if (platform == TargetPlatform.android) {
    await manager.deleteAllData();
  } else if (platform == TargetPlatform.iOS) {
    await manager.removeDataModifiedSince(
      dataTypes: WebsiteDataType.ALL,
      date: DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
    );
  }
}
