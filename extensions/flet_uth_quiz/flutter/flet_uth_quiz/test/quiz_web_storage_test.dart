import 'package:flet_uth_quiz/src/quiz_web_storage.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

class _RecordingStorageManager implements WebStorageManager {
  int deleteAllCalls = 0;
  Set<WebsiteDataType>? removedDataTypes;
  DateTime? removedSince;
  List<WebStorageOrigin> androidOrigins = <WebStorageOrigin>[];
  final List<String> deletedOrigins = <String>[];
  List<WebsiteDataRecord> iosRecords = <WebsiteDataRecord>[];
  Set<WebsiteDataType>? removedForTypes;
  List<WebsiteDataRecord>? removedForRecords;

  @override
  Future<void> deleteAllData() async {
    deleteAllCalls++;
  }

  @override
  Future<List<WebStorageOrigin>> getOrigins() async => androidOrigins;

  @override
  Future<void> deleteOrigin({required String origin}) async {
    deletedOrigins.add(origin);
  }

  @override
  Future<List<WebsiteDataRecord>> fetchDataRecords({
    required Set<WebsiteDataType> dataTypes,
  }) async =>
      iosRecords;

  @override
  Future<void> removeDataFor({
    required Set<WebsiteDataType> dataTypes,
    required List<WebsiteDataRecord> dataRecords,
  }) async {
    removedForTypes = dataTypes;
    removedForRecords = dataRecords;
  }

  @override
  Future<void> removeDataModifiedSince({
    required Set<WebsiteDataType> dataTypes,
    required DateTime date,
  }) async {
    removedDataTypes = dataTypes;
    removedSince = date;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  test('uses supported Android storage deletion', () async {
    final manager = _RecordingStorageManager();

    await purgeQuizWebStorage(TargetPlatform.android, manager);

    expect(manager.deleteAllCalls, 1);
    expect(manager.removedSince, isNull);
  });

  test('clears all iOS website data through the supported API', () async {
    final manager = _RecordingStorageManager();

    await purgeQuizWebStorage(TargetPlatform.iOS, manager);

    expect(manager.deleteAllCalls, 0);
    expect(manager.removedDataTypes, WebsiteDataType.ALL);
    expect(manager.removedSince?.millisecondsSinceEpoch, 0);
  });

  test('does not call mobile storage APIs on other platforms', () async {
    final manager = _RecordingStorageManager();

    await purgeQuizWebStorage(TargetPlatform.windows, manager);

    expect(manager.deleteAllCalls, 0);
    expect(manager.removedDataTypes, isNull);
  });

  test('purgeQuizOriginWebStorage deletes only trustedOrigin on Android', () async {
    final manager = _RecordingStorageManager()
      ..androidOrigins = <WebStorageOrigin>[
        WebStorageOrigin(origin: 'https://courses.ut.edu.vn'),
        WebStorageOrigin(origin: 'https://login.microsoftonline.com'),
        WebStorageOrigin(origin: 'https://portal.ut.edu.vn'),
      ];

    await purgeQuizOriginWebStorage(
      TargetPlatform.android,
      manager,
      Uri.parse('https://courses.ut.edu.vn/mod/quiz/attempt.php?id=1'),
    );

    expect(manager.deleteAllCalls, 0);
    expect(manager.deletedOrigins, <String>['https://courses.ut.edu.vn']);
  });

  test('purgeQuizOriginWebStorage excludes cookies and preserves other hosts on iOS', () async {
    final manager = _RecordingStorageManager()
      ..iosRecords = <WebsiteDataRecord>[
        WebsiteDataRecord(displayName: 'courses.ut.edu.vn'),
        WebsiteDataRecord(displayName: 'ut.edu.vn'),
        WebsiteDataRecord(displayName: 'microsoftonline.com'),
      ];

    await purgeQuizOriginWebStorage(
      TargetPlatform.iOS,
      manager,
      Uri.parse('https://courses.ut.edu.vn/mod/quiz/attempt.php?id=1'),
    );

    expect(manager.removedSince, isNull);
    expect(
      manager.removedForTypes?.contains(WebsiteDataType.WKWebsiteDataTypeCookies),
      isFalse,
    );
    expect(
      manager.removedForRecords?.map((r) => r.displayName).toList(),
      <String>['courses.ut.edu.vn'],
    );
  });
}
