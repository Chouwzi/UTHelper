import 'package:flet/flet.dart';
import 'package:flutter/widgets.dart';

import 'quiz_player_control.dart';
import 'quiz_session_cleaner.dart';

class Extension extends FletExtension {
  @override
  Widget? createWidget(Key? key, Control control) {
    return switch (control.type) {
      'UthQuizPlayer' => UthQuizPlayerControl(key: key, control: control),
      _ => null,
    };
  }

  @override
  FletService? createService(Control control) {
    return switch (control.type) {
      'UthQuizSessionCleaner' => UthQuizSessionCleanerService(
          control: control,
          activeWindowsProfilePaths: activeQuizWindowsProfilePaths,
        ),
      _ => null,
    };
  }
}
