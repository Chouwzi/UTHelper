import 'dart:async';
import 'dart:io';
import 'dart:ui';

import 'package:flutter/foundation.dart';

void main(List<String> args) async {
  FletDeepLinkingBootstrap.install();
  runApp(BootHost(args: args));
}
