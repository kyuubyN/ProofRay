import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import 'local_model_controller.dart';

/// Browses every GGUF repository on Hugging Face, live.
///
/// Opens straight into the catalogue. Where the files should land is asked at
/// download time instead of on the way in -- a file dialog appearing before a
/// single model has been seen reads as the wrong window opening, not as a step.
Future<void> showModelBrowser(
  BuildContext context,
  LocalModelController controller,
) async {
  unawaited(controller.loadCatalog());
  await showDialog<void>(
    context: context,
    builder: (BuildContext context) =>
        _ModelBrowserDialog(controller: controller),
  );
}

class _ModelBrowserDialog extends StatefulWidget {
  const _ModelBrowserDialog({required this.controller});

  final LocalModelController controller;

  @override
  State<_ModelBrowserDialog> createState() => _ModelBrowserDialogState();
}

class _ModelBrowserDialogState extends State<_ModelBrowserDialog> {
  final TextEditingController _search = TextEditingController();
  String _family = '';
  ModelSummary? _open;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  bool get _pt => Localizations.localeOf(context).languageCode == 'pt';

  Future<void> _selectFamily(ModelFamily family) async {
    setState(() {
      _family = family.key;
      _open = null;
      _search.text = family.key;
    });
    await widget.controller.search(family.key);
  }

  Future<void> _runSearch() async {
    setState(() {
      _family = '';
      _open = null;
    });
    await widget.controller.search(_search.text.trim());
  }

  Future<void> _open_(ModelSummary model) async {
    setState(() => _open = model);
    await widget.controller.openRepository(model.repository);
  }

  @override
  Widget build(BuildContext context) => Dialog(
    backgroundColor: ProofRayColors.paper,
    shape: const RoundedRectangleBorder(
      side: BorderSide(color: ProofRayColors.ink),
      borderRadius: BorderRadius.all(Radius.circular(2)),
    ),
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 900, maxHeight: 620),
      child: ListenableBuilder(
        listenable: widget.controller,
        builder: (BuildContext context, Widget? child) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            _header(context),
            const Divider(height: 1),
            Expanded(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  SizedBox(width: 170, child: _familyRail()),
                  const VerticalDivider(width: 1),
                  Expanded(child: _open == null ? _results() : _files()),
                ],
              ),
            ),
            if (widget.controller.downloadLabel != null) _progress(),
            const Divider(height: 1),
            _footer(context),
          ],
        ),
      ),
    ),
  );

  Widget _header(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(20, 14, 12, 12),
    child: Row(
      children: <Widget>[
        Expanded(
          child: TextField(
            controller: _search,
            onSubmitted: (_) => unawaited(_runSearch()),
            decoration: InputDecoration(
              isDense: true,
              labelText: _pt
                  ? 'Buscar modelos no Hugging Face'
                  : 'Search models on Hugging Face',
              prefixIcon: const Icon(Icons.search, size: 18),
              suffixIcon: IconButton(
                tooltip: _pt ? 'Buscar' : 'Search',
                onPressed: () => unawaited(_runSearch()),
                icon: const Icon(Icons.arrow_forward, size: 16),
              ),
            ),
          ),
        ),
        IconButton(
          onPressed: () => Navigator.of(context).pop(),
          icon: const Icon(Icons.close, size: 18),
        ),
      ],
    ),
  );

  Widget _familyRail() => ColoredBox(
    color: ProofRayColors.softPaper,
    child: ListView(
      padding: const EdgeInsets.symmetric(vertical: 6),
      children: <Widget>[
        for (final ModelFamily family in widget.controller.families)
          ListTile(
            dense: true,
            selected: family.key == _family && _open == null,
            selectedTileColor: ProofRayColors.paper,
            selectedColor: ProofRayColors.ink,
            title: Text(family.label),
            onTap: () => unawaited(_selectFamily(family)),
          ),
      ],
    ),
  );

  Widget _results() {
    if (widget.controller.busy) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 1));
    }
    final List<ModelSummary> results = widget.controller.searchResults;
    if (results.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Text(
            widget.controller.error ??
                (_pt ? 'Nada encontrado' : 'Nothing found'),
            textAlign: TextAlign.center,
          ),
        ),
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: results.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (BuildContext context, int index) {
        final ModelSummary model = results[index];
        return ListTile(
          dense: true,
          title: Text(model.name, overflow: TextOverflow.ellipsis),
          subtitle: Text(
            '${model.owner} · ${model.downloadsLabel} '
            '${_pt ? "downloads" : "downloads"} · ${model.updated}',
            style: const TextStyle(fontFamily: 'monospace', fontSize: 9),
            overflow: TextOverflow.ellipsis,
          ),
          trailing: const Icon(Icons.chevron_right, size: 18),
          onTap: () => unawaited(_open_(model)),
        );
      },
    );
  }

  Widget _files() {
    final ModelSummary model = _open!;
    final bool stale =
        widget.controller.catalogRepository != model.repository;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(8, 6, 16, 4),
          child: Row(
            children: <Widget>[
              IconButton(
                tooltip: _pt ? 'Voltar' : 'Back',
                onPressed: () => setState(() => _open = null),
                icon: const Icon(Icons.arrow_back, size: 18),
              ),
              Expanded(
                child: Text(
                  model.repository,
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 11,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: widget.controller.busy || stale
              ? const Center(child: CircularProgressIndicator(strokeWidth: 1))
              : _quantisations(),
        ),
      ],
    );
  }

  Widget _quantisations() {
    final List<CatalogModelFile> files = widget.controller.catalogFiles;
    if (files.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(
            widget.controller.error ??
                (_pt
                    ? 'Nenhum GGUF com checksum publicado'
                    : 'No GGUF with a published checksum'),
            textAlign: TextAlign.center,
          ),
        ),
      );
    }
    final bool downloading = widget.controller.downloadLabel != null;
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: files.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (BuildContext context, int index) {
        final CatalogModelFile file = files[index];
        return ListTile(
          dense: true,
          title: Text(file.quantisation),
          subtitle: Text(
            '${file.sizeLabel} · ${file.filename}',
            style: const TextStyle(fontFamily: 'monospace', fontSize: 9),
            overflow: TextOverflow.ellipsis,
          ),
          trailing: OutlinedButton(
            onPressed: downloading
                ? null
                : () => unawaited(
                    widget.controller.downloadCatalogModel(file),
                  ),
            child: Text(_pt ? 'Baixar' : 'Download'),
          ),
        );
      },
    );
  }

  Widget _progress() => Padding(
    padding: const EdgeInsets.fromLTRB(20, 12, 20, 12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          widget.controller.downloadLabel!,
          style: const TextStyle(fontFamily: 'monospace', fontSize: 10),
          overflow: TextOverflow.ellipsis,
        ),
        const SizedBox(height: 6),
        LinearProgressIndicator(
          // Indeterminate until a total is known, rather than a bar that
          // pretends to be at zero percent of an unknown size.
          value: widget.controller.downloadFraction,
          minHeight: 6,
          backgroundColor: ProofRayColors.softPaper,
          color: ProofRayColors.ink,
        ),
        const SizedBox(height: 4),
        Text(
          _byteLabel(widget.controller, _pt),
          style: const TextStyle(fontFamily: 'monospace', fontSize: 10),
        ),
      ],
    ),
  );

  Widget _footer(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(20, 10, 12, 10),
    child: Row(
      children: <Widget>[
        Expanded(
          child: Text(
            widget.controller.directory == null
                ? (_pt
                      ? 'Verificado por SHA-256. Salvo na pasta de modelos do ProofRay.'
                      : 'Verified by SHA-256. Saved to ProofRay’s model folder.')
                : '${_pt ? "Salvando em" : "Saving to"} ${widget.controller.directory}',
            style: const TextStyle(fontFamily: 'monospace', fontSize: 10),
            overflow: TextOverflow.ellipsis,
          ),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(_pt ? 'Fechar' : 'Close'),
        ),
      ],
    ),
  );
}

String _byteLabel(LocalModelController controller, bool pt) {
  double gb(int bytes) => bytes / 1000000000;
  if (controller.downloadTotal <= 0) {
    return pt ? 'baixando…' : 'downloading…';
  }
  return '${gb(controller.downloadReceived).toStringAsFixed(2)} / '
      '${gb(controller.downloadTotal).toStringAsFixed(2)} GB';
}
