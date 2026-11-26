import 'dart:async';
import 'dart:io';

import 'package:flet_uth_quiz/src/quiz_profile_storage.dart';
import 'package:flet_uth_quiz/src/quiz_session_cleaner.dart';
import 'package:flutter/foundation.dart';
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

  test('QuizWindowsEnvironmentCache reuses environment across prewarm and quiz opens, protects active mounts, and disposes only on eviction or logout', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_env_cache_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final activePaths = <String>{};
    final cache = QuizWindowsEnvironmentCache<String>(
      maxCachedEnvironments: 2,
      activeProfilePaths: activePaths,
    );
    final created = <String>[];
    final disposed = <String>[];

    Future<String> createEnv(Directory dir) async {
      final token = 'env:${path.basename(dir.path)}#${created.length + 1}';
      created.add(token);
      return token;
    }

    Future<void> disposeEnv(String env) async {
      disposed.add(env);
    }

    final key1 = 'a' * 64;
    final key2 = 'b' * 64;
    final key3 = 'c' * 64;

    // 1. Prewarm creates once with mountCount=0, then open reuses without recreating.
    final prewarmed = await cache.acquire(
      key1,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: false,
    );
    final opened1 = await cache.acquire(
      key1,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: true,
    );
    expect(opened1.environment, prewarmed.environment);
    expect(created, hasLength(1));

    // Releasing mount preserves environment & profile folder for HTTP cache reuse.
    cache.releaseMount(key1);
    await sweepQuizProfileDirectories(
      root,
      activePaths: activePaths,
      retryDelays: const [Duration(milliseconds: 1)],
    );
    expect(await opened1.profileDirectory.exists(), isTrue);
    expect(disposed, isEmpty);

    // 2. Active mount on key2 is protected when key3 overflows maxCachedEnvironments=2.
    final opened2 = await cache.acquire(
      key2,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: true,
    );
    // Touch key1 as inactive, keep key2 active, then add key3 -> key1 (inactive) evicted.
    await cache.acquire(
      key3,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: false,
    );
    expect(cache.containsKey(key1), isFalse);
    expect(cache.containsKey(key2), isTrue);
    expect(cache.containsKey(key3), isTrue);
    expect(disposed, [prewarmed.environment]);

    // 3. Logout cleanup disposes inactive (key3) before sweep, preserving active (key2).
    await cache.disposeInactive(disposeEnvironment: disposeEnv);
    await sweepQuizProfileDirectories(
      root,
      activePaths: activePaths,
      retryDelays: const [Duration(milliseconds: 1)],
    );
    expect(cache.containsKey(key3), isFalse);
    expect(cache.containsKey(key2), isTrue);
    expect(await opened2.profileDirectory.exists(), isTrue);

    cache.releaseMount(key2);
    await cache.disposeInactive(disposeEnvironment: disposeEnv);
    await sweepQuizProfileDirectories(
      root,
      activePaths: activePaths,
      retryDelays: const [Duration(milliseconds: 1)],
    );
    expect(cache.isEmpty, isTrue);
    expect(await opened2.profileDirectory.exists(), isFalse);
  });

  test('QuizWindowsEnvironmentCache removes failed creation from cache', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_env_err_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final activePaths = <String>{};
    final cache = QuizWindowsEnvironmentCache<String>(
      activeProfilePaths: activePaths,
    );
    final key = 'd' * 64;
    var attempts = 0;

    await expectLater(
      cache.acquire(
        key,
        root,
        createEnvironment: (_) async {
          attempts += 1;
          throw StateError('boom');
        },
        markActiveMount: true,
      ),
      throwsStateError,
    );
    expect(cache.isEmpty, isTrue);
    expect(activePaths, isEmpty);

    final recovered = await cache.acquire(
      key,
      root,
      createEnvironment: (_) async {
        attempts += 1;
        return 'ok';
      },
      markActiveMount: true,
    );
    expect(recovered.environment, 'ok');
    expect(attempts, 2);
  });

  test('prewarmQuizWebView validates session key and populates cache on Windows', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_prewarm_');
    addTearDown(() async {
      debugDefaultTargetPlatformOverride = null;
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    expect(await prewarmQuizWebView(sessionKey: null), isFalse);
    expect(await prewarmQuizWebView(sessionKey: 'short'), isFalse);

    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    final key = 'e' * 64;
    expect(
      await prewarmQuizWebView(
        sessionKey: key,
        getWindowsRuntimeVersion: () async => null,
      ),
      isFalse,
    );
  });

  test('concurrent prewarm and mount acquire on uncached key coalesces into one environment with activeMountCount 1', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_env_race1_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final activePaths = <String>{};
    final cache = QuizWindowsEnvironmentCache<String>(
      maxCachedEnvironments: 2,
      activeProfilePaths: activePaths,
    );
    final key = '1' * 64;
    final gate = Completer<void>();
    var createCalls = 0;
    final disposed = <String>[];

    Future<String> createEnv(Directory dir) async {
      createCalls += 1;
      await gate.future;
      return 'env#$createCalls:${path.basename(dir.path)}';
    }

    final prewarmFuture = cache.acquire(
      key,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: (env) async => disposed.add(env),
      markActiveMount: false,
    );
    final mountFuture = cache.acquire(
      key,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: (env) async => disposed.add(env),
      markActiveMount: true,
    );

    expect(cache.activeMountCountFor(key), 1);
    gate.complete();
    final results = await Future.wait([prewarmFuture, mountFuture]);

    expect(createCalls, 1);
    expect(results[0].environment, results[1].environment);
    expect(cache.activeMountCountFor(key), 1);

    await cache.disposeInactive(
      disposeEnvironment: (env) async => disposed.add(env),
    );
    expect(disposed, isEmpty);
    expect(cache.containsKey(key), isTrue);
    expect(activePaths, contains(normalizeProfilePath(results[1].profileDirectory.path)));
  });

  test('releaseMount during pending directory/environment creation clears activeMountCount so logout disposes and sweeps cleanly', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_env_race2_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final activePaths = <String>{};
    final cache = QuizWindowsEnvironmentCache<String>(
      maxCachedEnvironments: 2,
      activeProfilePaths: activePaths,
    );
    final key = '2' * 64;
    final createGate = Completer<void>();
    final createStarted = Completer<void>();
    final disposed = <String>[];

    final acquireFuture = cache.acquire(
      key,
      root,
      createEnvironment: (_) async {
        if (!createStarted.isCompleted) createStarted.complete();
        await createGate.future;
        return 'env#late';
      },
      disposeEnvironment: (env) async => disposed.add(env),
      markActiveMount: true,
    );

    // Release happens before createEnvironment even resolves (e.g., mid-Directory.create dispose).
    cache.releaseMount(key);
    expect(cache.activeMountCountFor(key), 0);

    createGate.complete();
    await createStarted.future;
    final acquired = await acquireFuture;
    expect(cache.activeMountCountFor(key), 0);

    await cache.disposeInactive(
      disposeEnvironment: (env) async => disposed.add(env),
    );
    await sweepQuizProfileDirectories(
      root,
      activePaths: activePaths,
      retryDelays: const [Duration(milliseconds: 1)],
    );

    expect(disposed, ['env#late']);
    expect(cache.isEmpty, isTrue);
    expect(activePaths, isEmpty);
    expect(await acquired.profileDirectory.exists(), isFalse);
  });

  test('re-acquiring key1 while evicted key1 disposal is still awaiting preserves key1 in activeProfilePaths', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_env_race3_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final activePaths = <String>{};
    final cache = QuizWindowsEnvironmentCache<String>(
      maxCachedEnvironments: 2,
      activeProfilePaths: activePaths,
    );
    final key1 = '3' * 64;
    final key2 = '4' * 64;
    final key3 = '5' * 64;
    final disposeStarted = Completer<void>();
    final finishDispose = Completer<void>();
    var seq = 0;

    Future<String> createEnv(Directory dir) async {
      seq += 1;
      return 'env:${path.basename(dir.path)}#$seq';
    }

    Future<void> disposeEnv(String env) async {
      if (env.endsWith('#1')) {
        if (!disposeStarted.isCompleted) disposeStarted.complete();
        await finishDispose.future;
      }
    }

    final first1 = await cache.acquire(
      key1,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: false,
    );
    await cache.acquire(
      key2,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: true,
    );

    final evictingKey3 = cache.acquire(
      key3,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: false,
    );
    await disposeStarted.future;

    final reacquired1 = await cache.acquire(
      key1,
      root,
      createEnvironment: createEnv,
      disposeEnvironment: disposeEnv,
      markActiveMount: true,
    );
    finishDispose.complete();
    await evictingKey3;

    final key1Path = normalizeProfilePath(first1.profileDirectory.path);
    expect(reacquired1.profileDirectory.path, first1.profileDirectory.path);
    expect(activePaths, contains(key1Path));
  });

  test('rapid close then reopen on same sessionKey skips stale deleteAllCookies and disposeInactive awaits close cleanup', () async {
    final root = await Directory.systemTemp.createTemp('uth_quiz_env_race4_');
    addTearDown(() async {
      if (await root.exists()) {
        await root.delete(recursive: true);
      }
    });

    final activePaths = <String>{};
    final cache = QuizWindowsEnvironmentCache<String>(
      maxCachedEnvironments: 2,
      activeProfilePaths: activePaths,
    );
    final key = '6' * 64;
    final sessionWorkGate = Completer<void>();
    final sessionWorkStarted = Completer<void>();
    final events = <String>[];

    // Part A: Quiz A opens -> closes -> Quiz B reopens while Quiz A's session work is still running.
    final quizA = await cache.acquire(
      key,
      root,
      createEnvironment: (_) async => 'env#1',
      markActiveMount: true,
    );
    cache.releaseMount(key);
    final cleanupAFuture = cache.runCloseCleanup(
      key,
      expectedEpoch: quizA.sessionEpoch,
      beforeCleanup: () async {
        if (!sessionWorkStarted.isCompleted) sessionWorkStarted.complete();
        await sessionWorkGate.future;
      },
      cleanup: () async {
        events.add('stale_deleteAllCookies_A');
      },
    );
    await sessionWorkStarted.future;

    // Quiz B reopens concurrently on the same sessionKey.
    final quizBFuture = cache.acquire(
      key,
      root,
      createEnvironment: (_) async => 'env#1',
      markActiveMount: true,
    );
    expect(cache.sessionEpochFor(key) > quizA.sessionEpoch, isTrue);

    sessionWorkGate.complete();
    await Future.wait([cleanupAFuture, quizBFuture]);
    expect(events, isEmpty);

    // Part B: Quiz B closes cleanly, cleanup runs, and disposeInactive awaits cleanup before disposeEnvironment.
    final cleanupGate = Completer<void>();
    final cleanupEntered = Completer<void>();
    final quizB = await quizBFuture;
    cache.releaseMount(key);

    final cleanupBFuture = cache.runCloseCleanup(
      key,
      expectedEpoch: quizB.sessionEpoch,
      cleanup: () async {
        if (!cleanupEntered.isCompleted) cleanupEntered.complete();
        await cleanupGate.future;
        events.add('deleteAllCookies_B');
      },
    );
    await cleanupEntered.future;

    final disposeInactiveFuture = cache.disposeInactive(
      disposeEnvironment: (env) async {
        events.add('dispose:$env');
      },
    );
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(events, isEmpty);

    cleanupGate.complete();
    await Future.wait([cleanupBFuture, disposeInactiveFuture]);
    expect(events, ['deleteAllCookies_B', 'dispose:env#1']);
  });
}
