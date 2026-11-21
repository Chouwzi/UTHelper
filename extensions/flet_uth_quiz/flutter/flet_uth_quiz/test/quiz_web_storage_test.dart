import 'package:flet_uth_quiz/src/quiz_web_storage.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:flutter_test/flutter_test.dart';

class _RecordingStorageManager implements WebStorageManager {
  int deleteAllCalls = 0;
  Set<WebsiteDataType>? removedDataTypes;
  DateTime? removedSince;

  @override
  Future<void> deleteAllData() async {
    deleteAllCalls++;
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
}
