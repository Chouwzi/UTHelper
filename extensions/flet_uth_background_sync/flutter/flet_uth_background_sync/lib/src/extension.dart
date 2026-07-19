import 'package:flet/flet.dart';

import 'service.dart';

class Extension extends FletExtension {
  @override
  FletService? createService(Control control) {
    return switch (control.type) {
      'UthBackgroundSync' => UthBackgroundSyncService(control: control),
      _ => null,
    };
  }
}
