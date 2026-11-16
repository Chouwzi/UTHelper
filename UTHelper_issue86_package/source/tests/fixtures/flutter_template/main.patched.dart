import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui';

import 'package:flutter/foundation.dart';

// UTHELPER_DIAGNOSTICS_PATCH_BEGIN_V1
const int _utHelperFlutterBridgeMaxBytes = 64 * 1024;
bool _utHelperFlutterDiagnosticsInstalled = false;
String _utHelperFlutterPhase = 'boot';

String _utHelperSafeSymbol(Object? value, {String fallback = 'unknown'}) {
  final raw = value?.toString() ?? '';
  final normalized = raw
      .replaceAll(RegExp(r'[^A-Za-z0-9_.<>-]+'), '_')
      .replaceAll(RegExp(r'^[._-]+|[._-]+$'), '');
  if (normalized.isEmpty) return fallback;
  final prefixed = RegExp(r'^[A-Za-z_]').hasMatch(normalized)
      ? normalized
      : '_$normalized';
  return prefixed.length <= 128 ? prefixed : prefixed.substring(0, 128);
}

List<String> _utHelperStackSymbols(StackTrace? stack) {
  if (stack == null) return const <String>[];
  final symbols = <String>[];
  final matcher = RegExp(r'^\s*#\d+\s+([A-Za-z0-9_.$<>-]+)');
  for (final line in stack.toString().split('\n')) {
    final match = matcher.firstMatch(line);
    if (match == null) continue;
    final symbol = _utHelperSafeSymbol(match.group(1));
    if (symbol != 'unknown') symbols.add(symbol);
    if (symbols.length == 16) break;
  }
  return symbols;
}

String _utHelperCoarseUtcMinute() {
  final now = DateTime.now().toUtc();
  return DateTime.utc(now.year, now.month, now.day, now.hour, now.minute)
      .toIso8601String();
}

void _utHelperWriteFlutterError(
  Object error,
  StackTrace? stack,
) {
  if (!Platform.isWindows) return;
  try {
    final appData = Platform.environment['APPDATA'];
    if (appData == null || appData.isEmpty) return;
    final appRoot = Directory('$appData\\UTHelper');
    appRoot.createSync(recursive: true);
    if (FileSystemEntity.typeSync(appRoot.path, followLinks: false) !=
        FileSystemEntityType.directory) {
      return;
    }
    final directory = Directory('${appRoot.path}\\diagnostics');
    directory.createSync(recursive: true);
    if (FileSystemEntity.typeSync(directory.path, followLinks: false) !=
        FileSystemEntityType.directory) {
      return;
    }
    final bridge = File('${directory.path}\\flutter-errors.jsonl');
    final bridgeType =
        FileSystemEntity.typeSync(bridge.path, followLinks: false);
    if (bridgeType != FileSystemEntityType.notFound &&
        bridgeType != FileSystemEntityType.file) {
      return;
    }
    final record = '${jsonEncode(<String, Object>{
      'runtime_type': _utHelperSafeSymbol(error.runtimeType),
      'symbols': _utHelperStackSymbols(stack),
      'phase': _utHelperFlutterPhase,
      'occurred_at': _utHelperCoarseUtcMinute(),
    })}\n';
    final encodedLength = utf8.encode(record).length;
    if (encodedLength > _utHelperFlutterBridgeMaxBytes) return;
    var currentLength = 0;
    if (bridge.existsSync()) currentLength = bridge.lengthSync();
    final append = currentLength + encodedLength <=
        _utHelperFlutterBridgeMaxBytes;
    bridge.writeAsStringSync(
      record,
      mode: append ? FileMode.append : FileMode.write,
      flush: true,
    );
  } catch (_) {
    // Diagnostics must never interfere with the Flutter runner.
  }
}

void _utHelperInstallFlutterDiagnostics() {
  if (!Platform.isWindows || _utHelperFlutterDiagnosticsInstalled) return;
  _utHelperFlutterDiagnosticsInstalled = true;
  final previousFlutterHandler = FlutterError.onError;
  FlutterError.onError = (FlutterErrorDetails details) {
    _utHelperWriteFlutterError(details.exception, details.stack);
    if (previousFlutterHandler != null) {
      previousFlutterHandler(details);
    } else {
      FlutterError.presentError(details);
    }
  };
  final previousPlatformHandler = PlatformDispatcher.instance.onError;
  PlatformDispatcher.instance.onError = (Object error, StackTrace stack) {
    _utHelperWriteFlutterError(error, stack);
    return previousPlatformHandler?.call(error, stack) ?? false;
  };
}
// UTHELPER_DIAGNOSTICS_PATCH_END_V1

void main(List<String> args) async {
  _utHelperInstallFlutterDiagnostics();
  Timer.run(() => _utHelperFlutterPhase = 'gui');
  FletDeepLinkingBootstrap.install();
  runApp(BootHost(args: args));
}
