import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../services/bridge/proofray_bridge.dart';
import '../../storage/conversation_store.dart';

class MemoryScreen extends StatefulWidget {
  const MemoryScreen({required this.store, required this.bridge, super.key});

  final ConversationStore store;
  final ProofRayBridge? Function() bridge;

  @override
  State<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends State<MemoryScreen> {
  late Future<List<MemoryObservation>> _rows;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => _rows = widget.store.memoryObservations();

  Future<void> _purge(MemoryObservation observation) async {
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) {
      return;
    }
    final AppStrings strings = AppStrings.of(context);
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text(strings.removeAuthorizedMemory),
        content: Text(strings.removeMemoryExplanation),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(strings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(strings.remove),
          ),
        ],
      ),
    );
    if (!mounted) return;
    if (confirmed != true) {
      return;
    }
    final Map<String, Object?> result = await bridge.purgeMemorySource(
      observation.sourceId,
    );
    if (!mounted) return;
    if (result['state'] == 'PURGED') {
      await widget.store.markMemoryPurged(observation.messageId);
      setState(_reload);
    }
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return Column(
      children: <Widget>[
        Container(
          height: 68,
          padding: const EdgeInsets.symmetric(horizontal: 24),
          alignment: Alignment.centerLeft,
          decoration: const BoxDecoration(
            border: Border(bottom: BorderSide(color: ProofRayColors.hairline)),
          ),
          child: Text(
            strings.authorizedMemory,
            style: Theme.of(context).textTheme.headlineSmall,
          ),
        ),
        Expanded(
          child: FutureBuilder<List<MemoryObservation>>(
            future: _rows,
            builder:
                (
                  BuildContext context,
                  AsyncSnapshot<List<MemoryObservation>> snapshot,
                ) {
                  final List<MemoryObservation> rows =
                      snapshot.data ?? const <MemoryObservation>[];
                  if (snapshot.connectionState != ConnectionState.done) {
                    return const Center(
                      child: CircularProgressIndicator(strokeWidth: 1),
                    );
                  }
                  if (rows.isEmpty) {
                    return Center(child: Text(strings.noAuthorizedMemory));
                  }
                  return ListView.separated(
                    padding: const EdgeInsets.all(20),
                    itemCount: rows.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (BuildContext context, int index) {
                      final MemoryObservation row = rows[index];
                      return ListTile(
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 4,
                          vertical: 8,
                        ),
                        leading: const Icon(Icons.verified_outlined),
                        title: Text(row.text),
                        subtitle: Text(
                          row.sourceId,
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 10,
                          ),
                        ),
                        trailing: IconButton(
                          tooltip: strings.removeFromMemory,
                          onPressed: () => _purge(row),
                          icon: const Icon(Icons.delete_outline),
                        ),
                      );
                    },
                  );
                },
          ),
        ),
      ],
    );
  }
}
