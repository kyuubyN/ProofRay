import 'dart:async';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../services/bridge/proofray_bridge.dart';
import 'llama_installer_dialog.dart';
import 'local_model_controller.dart';
import 'model_browser_dialog.dart';

/// Picks the folder of GGUF files and says which one is in VRAM.
class LocalModelPanel extends StatelessWidget {
  const LocalModelPanel({
    required this.controller,
    required this.bridge,
    super.key,
  });

  final LocalModelController controller;
  final ProofRayBridge? Function() bridge;

  Future<void> _pickBinary() async {
    final XFile? file = await openFile();
    if (file == null) return;
    await controller.useServerBinary(file.path);
  }

  Future<void> _pickDirectory() async {
    final String? directory = await getDirectoryPath();
    if (directory == null) return;
    await controller.useDirectory(directory);
  }

  @override
  Widget build(BuildContext context) {
    final bool pt = Localizations.localeOf(context).languageCode == 'pt';
    return ListenableBuilder(
      listenable: controller,
      builder: (BuildContext context, Widget? child) {
        final List<LocalModelEntry> unsupported = controller.models
            .where((LocalModelEntry item) => !item.supported)
            .toList();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              pt
                  ? 'Os modelos rodam na sua máquina através de uma build do llama.cpp. Nada sai do computador.'
                  : 'Models run on your machine through a llama.cpp build. Nothing leaves the computer.',
            ),
            const SizedBox(height: 12),
            // The engine first: without llama.cpp there is nothing to load a
            // model into, so its state is stated before any model is offered.
            InputDecorator(
              decoration: InputDecoration(
                labelText: pt ? 'Motor llama.cpp' : 'llama.cpp engine',
              ),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      controller.serverBinary ??
                          (pt
                              ? 'Não instalado — busca automática no PATH'
                              : 'Not installed — auto-detected on PATH'),
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 11,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton(
                    onPressed: () => unawaited(
                      showLlamaInstaller(context, controller, bridge),
                    ),
                    child: Text(pt ? 'Instalar' : 'Install'),
                  ),
                  TextButton(
                    onPressed: () => unawaited(_pickBinary()),
                    child: Text(pt ? 'Apontar' : 'Point to it'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            InputDecorator(
              decoration: InputDecoration(
                labelText: pt ? 'Pasta de modelos' : 'Model folder',
              ),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      controller.directory ??
                          (pt ? 'Resolvendo…' : 'Resolving…'),
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 12,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton(
                    onPressed: () => unawaited(_pickDirectory()),
                    child: Text(pt ? 'Escolher' : 'Choose'),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 6),
            // Always offered, including before a folder exists: someone with no
            // models yet is exactly who needs the catalogue, and hiding it
            // behind the folder made the two depend on each other.
            Wrap(
              spacing: 8,
              children: <Widget>[
                FilledButton.icon(
                  onPressed: () =>
                      unawaited(showModelBrowser(context, controller)),
                  icon: const Icon(Icons.download_outlined, size: 16),
                  label: Text(pt ? 'Baixar modelos' : 'Download models'),
                ),
                if (controller.directory != null)
                  TextButton.icon(
                    onPressed: controller.busy
                        ? null
                        : () => unawaited(controller.scan()),
                    icon: const Icon(Icons.refresh, size: 16),
                    label: Text(pt ? 'Reexaminar pasta' : 'Rescan folder'),
                  ),
              ],
            ),
            if (controller.error != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  controller.error!,
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 11,
                  ),
                ),
              ),
            const SizedBox(height: 8),
            for (final LocalModelEntry model in controller.usableModels)
              ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                leading: Icon(
                  model.path == controller.activePath &&
                          controller.state == 'ready'
                      ? Icons.memory
                      : Icons.storage_outlined,
                  size: 18,
                ),
                title: Text(model.name, overflow: TextOverflow.ellipsis),
                subtitle: Text(
                  '${model.format.toUpperCase()} · ${model.sizeLabel}',
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 10,
                  ),
                ),
                trailing: model.path == controller.activePath
                    ? TextButton(
                        onPressed: controller.busy
                            ? null
                            : () => unawaited(controller.unload()),
                        child: Text(pt ? 'Descarregar' : 'Unload'),
                      )
                    : FilledButton(
                        onPressed: controller.busy
                            ? null
                            : () => unawaited(controller.load(model)),
                        child: Text(pt ? 'Carregar' : 'Load'),
                      ),
              ),
            if (controller.directory != null &&
                controller.usableModels.isEmpty &&
                !controller.busy)
              Text(
                pt
                    ? 'Nenhum arquivo GGUF encontrado nessa pasta.'
                    : 'No GGUF file found in that folder.',
              ),
            if (unsupported.isNotEmpty) ...<Widget>[
              const SizedBox(height: 10),
              Text(
                pt
                    ? 'Ignorados (o llama.cpp carrega apenas GGUF):'
                    : 'Skipped (llama.cpp loads GGUF only):',
                style: Theme.of(context).textTheme.labelSmall,
              ),
              for (final LocalModelEntry model in unsupported.take(6))
                Text(
                  '· ${model.name} (${model.format})',
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 10,
                    color: ProofRayColors.ink,
                  ),
                ),
            ],
          ],
        );
      },
    );
  }
}
