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
}
