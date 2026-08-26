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

  // Block body on purpose: `void` is erased at runtime, so an arrow body
  // still hands setState the assigned Future and Flutter throws on it.
  void _reload() {
    _rows = widget.store.memoryObservations();
  }

  Future<void> _purge(MemoryObservation observation) async {
    final AppStrings strings = AppStrings.of(context);
    final ProofRayBridge? bridge = widget.bridge();
    if (bridge == null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('${strings.remove}: local_core_unavailable')),
      );
      return;
    }
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
    // Every outcome below has to reach the screen. This used to return
    // silently on a null bridge, on any non-PURGED state, and -- because the
    // onPressed callback discards the future -- on a thrown bridge error too,
    // so a failed purge was indistinguishable from a button that does nothing.
    String? failure;
    try {
      final Map<String, Object?> result = await bridge.purgeMemorySource(
        observation.sourceId,
      );
      final Object? state = result['state'];
      if (state == 'PURGED' || state == 'REJECTED_NOT_FOUND') {
        // NOT_FOUND means the durable field no longer holds this source while
        // the local row still claims it does: the local flag is the stale one,
        // so clearing it is the repair, not a failure to report.
        await widget.store.markMemoryPurged(observation.messageId);
        if (!mounted) return;
        setState(_reload);
        return;
      }
      failure = state is String ? state : 'unknown_state';
    } on ProofRayBridgeException catch (error) {
      failure = error.code;
    } on Object catch (error) {
      failure = error.toString();
    }
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('${strings.remove}: $failure')));
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
