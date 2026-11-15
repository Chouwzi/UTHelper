import 'dart:async';

import 'package:flet_uth_quiz/src/android_session_queue.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test(
    'disposing a queued session does not bypass the active session',
    () async {
      final queue = AndroidSessionQueue();
      final first = await queue.acquire(cancelled: Completer<void>().future);
      expect(first, isNotNull);

      final cancelSecond = Completer<void>();
      final second = queue.acquire(cancelled: cancelSecond.future);
      await Future<void>.delayed(Duration.zero);

      var thirdCompleted = false;
      final thirdFuture = queue
          .acquire(cancelled: Completer<void>().future)
          .then((lease) {
            thirdCompleted = true;
            return lease;
          });

      cancelSecond.complete();
      expect(await second, isNull);
      await Future<void>.delayed(Duration.zero);
      expect(thirdCompleted, isFalse);

      first!.release();
      final third = await thirdFuture.timeout(const Duration(seconds: 1));
      expect(third, isNotNull);
      third!.release();
    },
  );
}
