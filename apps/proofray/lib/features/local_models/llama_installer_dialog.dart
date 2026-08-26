import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../services/bridge/proofray_bridge.dart';
import 'local_model_controller.dart';

/// Offers the official llama.cpp builds that match this machine.
///
/// Each row is a real release asset, and the SHA-256 shown is the one GitHub
/// published for it -- the same value the download is checked against before
/// anything is unpacked or made executable. Nothing is fetched until a build is
/// picked and a destination chosen.
Future<void> showLlamaInstaller(
  BuildContext context,
  LocalModelController controller,
  ProofRayBridge? Function() bridge,
) async {
  await showDialog<void>(
    context: context,
    builder: (BuildContext context) =>
        _LlamaInstallerDialog(controller: controller, bridge: bridge),
  );
}

class _LlamaInstallerDialog extends StatefulWidget {
  const _LlamaInstallerDialog({required this.controller, required this.bridge});

  final LocalModelController controller;
  final ProofRayBridge? Function() bridge;

  @override
  State<_LlamaInstallerDialog> createState() => _LlamaInstallerDialogState();
}

class _LlamaInstallerDialogState extends State<_LlamaInstallerDialog> {
  List<Map<String, Object?>> _builds = const <Map<String, Object?>>[];
  String _platform = '';
  bool _busy = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) {
      setState(() {
        _busy = false;
        _error = 'local_core_unavailable';
      });
      return;
    }
    try {
      final Map<String, Object?> result = await bridge.llamaBuilds();
      final Object? builds = result['builds'];
      if (!mounted) return;
      setState(() {
        _platform =
            '${result['platform'] ?? ''} ${result['architecture'] ?? ''}'.trim();
        _builds = <Map<String, Object?>>[
          for (final Object? item in builds is List<Object?> ? builds : const <Object?>[])
            if (item is Map<String, Object?>) item,
        ];
      });
    } on ProofRayBridgeException catch (failure) {
      if (mounted) setState(() => _error = failure.code);
    } on Object catch (failure) {
      if (mounted) setState(() => _error = failure.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Installs straight away, into the app's own folder.
  ///
  /// Choosing a location is available from the settings panel for anyone who
  /// wants it, but it is not what "Install" should do: a file dialog opening
  /// instead of an install reads as the wrong window, not as a question.
  Future<void> _install(Map<String, Object?> build) async {
    final bool ok = await widget.controller.installLlama(build);
    if (!mounted) return;
    if (ok) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final bool pt = Localizations.localeOf(context).languageCode == 'pt';
    return Dialog(
      backgroundColor: ProofRayColors.paper,
      shape: const RoundedRectangleBorder(
        side: BorderSide(color: ProofRayColors.ink),
        borderRadius: BorderRadius.all(Radius.circular(2)),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 660, maxHeight: 540),
        child: ListenableBuilder(
          listenable: widget.controller,
          builder: (BuildContext context, Widget? child) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 16, 12, 12),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(
                            pt ? 'Instalar llama.cpp' : 'Install llama.cpp',
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          const SizedBox(height: 2),
                          Text(
                            _platform.isEmpty
                                ? ''
                                : (pt
                                      ? 'Builds oficiais para $_platform, verificadas por SHA-256.'
                                      : 'Official builds for $_platform, verified by SHA-256.'),
                            style: Theme.of(context).textTheme.labelSmall,
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      onPressed: () => Navigator.of(context).pop(),
                      icon: const Icon(Icons.close, size: 18),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Flexible(child: _body(pt)),
              if (widget.controller.downloadLabel != null) ...<Widget>[
                const Divider(height: 1),
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 12, 20, 14),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        widget.controller.downloadLabel!,
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 10,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 6),
                      LinearProgressIndicator(
                        value: widget.controller.downloadFraction,
                        minHeight: 6,
                        backgroundColor: ProofRayColors.softPaper,
                        color: ProofRayColors.ink,
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _body(bool pt) {
    if (_busy) {
      return const Padding(
        padding: EdgeInsets.all(32),
        child: Center(child: CircularProgressIndicator(strokeWidth: 1)),
      );
    }
    if (_error != null || _builds.isEmpty) {
      return Padding(
        padding: const EdgeInsets.all(28),
        child: Text(
          _error ??
              (pt
                  ? 'Nenhuma build oficial encontrada para esta máquina.'
                  : 'No official build found for this machine.'),
          textAlign: TextAlign.center,
        ),
      );
    }
    final bool downloading = widget.controller.downloadLabel != null;
    return ListView(
      shrinkWrap: true,
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      children: <Widget>[
        for (final Map<String, Object?> build in _builds)
          ListTile(
            contentPadding: EdgeInsets.zero,
            dense: true,
            title: Text(_variantLabel(build['variant'] as String? ?? '', pt)),
            subtitle: Text(
              '${_sizeLabel(build['size_bytes'] as int? ?? 0)} · '
              '${build['tag'] ?? ''} · '
              '${(build['sha256'] as String? ?? '').substring(0, 12)}…',
              style: const TextStyle(fontFamily: 'monospace', fontSize: 9),
              overflow: TextOverflow.ellipsis,
            ),
            trailing: OutlinedButton(
              onPressed: downloading ? null : () => unawaited(_install(build)),
              child: Text(pt ? 'Instalar' : 'Install'),
            ),
          ),
      ],
    );
  }
}

/// Names the accelerator in terms of what it runs on, not its SDK.
String _variantLabel(String variant, bool pt) => switch (variant) {
  'vulkan' => pt
      ? 'Vulkan — GPU (AMD, NVIDIA ou Intel)'
      : 'Vulkan — GPU (AMD, NVIDIA or Intel)',
  'cpu' => pt ? 'CPU — funciona em qualquer lugar' : 'CPU — works anywhere',
  'rocm' => 'ROCm — AMD',
  'cuda' => 'CUDA — NVIDIA',
  'sycl' => 'SYCL — Intel',
  'openvino' => 'OpenVINO — Intel',
  'opencl' => 'OpenCL',
  _ => variant,
};

String _sizeLabel(int bytes) => bytes >= 1000000000
    ? '${(bytes / 1000000000).toStringAsFixed(2)} GB'
    : '${(bytes / 1000000).toStringAsFixed(0)} MB';
