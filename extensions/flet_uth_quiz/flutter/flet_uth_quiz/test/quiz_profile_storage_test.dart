import 'dart:io';

import 'package:flet_uth_quiz/src/quiz_profile_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as path;

void main() {
  test('deletes profile directories and sweeps only inactive profiles', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_profiles_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final stale = Directory(path.join(root.path, 'stale-session'));
    final active = Directory(path.join(root.path, 'active-session'));
    await stale.create(recursive: true);
    await active.create(recursive: true);
    await File(path.join(stale.path, 'Cookies')).writeAsString('old');
    await File(path.join(active.path, 'Cookies')).writeAsString('keep');

    await sweepQuizProfileDirectories(
      root,
      activePaths: {active.path},
      retryDelays: const [Duration(milliseconds: 1)],
    );

    expect(await stale.exists(), isFalse);
    expect(await active.exists(), isTrue);
    expect(
      await deleteQuizProfileDirectoryWithRetry(
        active,
        retryDelays: const [Duration(milliseconds: 1)],
      ),
      isTrue,
    );
    expect(await active.exists(), isFalse);
  });

  test('fallback profile directory names are unique within the same microsecond',
      () {
    final first = buildFallbackQuizProfileDirectoryName(
      'ABCDEF0123456789',
      microsecondsSinceEpoch: 424242,
    );
    final second = buildFallbackQuizProfileDirectoryName(
      'ABCDEF0123456789',
      microsecondsSinceEpoch: 424242,
    );

    expect(first, isNot(equals(second)));
    expect(first, startsWith('abcdef0123456789-424242-'));
    expect(second, startsWith('abcdef0123456789-424242-'));
  });

  test('sweep checks live activePaths across async iterations', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_sweep_live_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final aStale = Directory(path.join(root.path, 'aaa-stale'));
    final bPromoted = Directory(path.join(root.path, 'bbb-promoted'));
    await aStale.create(recursive: true);
    await bPromoted.create(recursive: true);

    final activePaths = <String>{};
    final sweepFuture = sweepQuizProfileDirectories(
      root,
      activePaths: activePaths,
      retryDelays: const [Duration(milliseconds: 1)],
    );
    // Simulate another session registering bPromoted while sweep is iterating.
    activePaths.add(bPromoted.path);
    await sweepFuture;

    expect(await bPromoted.exists(), isTrue);
  });
}
