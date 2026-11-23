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

/// Removes only web storage scoped to [trustedOrigin] without wiping cookies or
/// storage belonging to other origins in the shared mobile WebView store.
Future<void> purgeQuizOriginWebStorage(
  TargetPlatform platform,
  WebStorageManager manager,
  Uri? trustedOrigin,
) async {
  if (trustedOrigin == null || trustedOrigin.host.isEmpty) return;
  final targetHost = trustedOrigin.host.toLowerCase();

  if (platform == TargetPlatform.android) {
    final origins = await manager.getOrigins();
    for (final item in origins) {
      final rawOrigin = item.origin;
      if (rawOrigin == null || rawOrigin.isEmpty) continue;
      final parsedHost = Uri.tryParse(rawOrigin)?.host.toLowerCase();
      if (parsedHost == targetHost) {
        await manager.deleteOrigin(origin: rawOrigin);
      }
    }
  } else if (platform == TargetPlatform.iOS) {
    final nonCookieTypes = WebsiteDataType.ALL
        .where((type) => type != WebsiteDataType.WKWebsiteDataTypeCookies)
        .toSet();
    final records = await manager.fetchDataRecords(dataTypes: nonCookieTypes);
    final matching = records.where((record) {
      final name = (record.displayName ?? '').toLowerCase();
      return name == targetHost;
    }).toList();
    if (matching.isNotEmpty) {
      await manager.removeDataFor(
        dataTypes: nonCookieTypes,
        dataRecords: matching,
      );
    }
  }
}
