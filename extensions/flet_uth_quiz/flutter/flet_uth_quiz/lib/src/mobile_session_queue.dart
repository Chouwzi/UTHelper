import 'dart:async';

final sharedMobileSessionQueue = MobileSessionQueue();

/// Serializes mobile WebView sessions because their cookie stores are shared.
class MobileSessionQueue {
  Future<void> _tail = Future<void>.value();
  bool _available = true;

  Future<MobileSessionLease?> acquire({required Future<void> cancelled}) async {
    if (!_available) return null;

    final previous = _tail;
    final release = Completer<void>();
    _tail = previous.then((_) => release.future);

    final canAcquire = await Future.any<bool>([
      previous.then((_) => true),
      cancelled.then((_) => false),
    ]);
    if (!canAcquire || !_available) {
      release.complete();
      return null;
    }
    return MobileSessionLease._(release);
  }

  /// Prevents another account session if storage cleanup failed or timed out.
  void disable() => _available = false;
}

class MobileSessionLease {
  MobileSessionLease._(this._release);

  final Completer<void> _release;

  void release() {
    if (!_release.isCompleted) _release.complete();
  }
}
