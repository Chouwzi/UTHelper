import 'package:flet/flet.dart';
import 'package:flutter/services.dart';

class UthBackgroundSyncService extends FletService {
  UthBackgroundSyncService({required super.control});

  static const MethodChannel _channel =
      MethodChannel('com.uthelper/background_sync');

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) {
    return _channel.invokeMethod<dynamic>(name, args);
  }

  @override
  void dispose() {
    control.removeInvokeMethodListener(_invokeMethod);
    super.dispose();
  }
}
