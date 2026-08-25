import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';
import '../../storage/conversation_store.dart';

class HistoryScreen extends StatefulWidget {
  const HistoryScreen({
    required this.store,
    required this.profileId,
    required this.activeConversationId,
    required this.onOpen,
    required this.onCreate,
    required this.onDelete,
    super.key,
  });

  final ConversationStore store;
  final String profileId;
  final String activeConversationId;
  final ValueChanged<ConversationSummary> onOpen;
  final VoidCallback onCreate;
  final Future<void> Function(
    ConversationSummary conversation,
    bool purgeMemory,
  )
  onDelete;

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  late Future<List<ConversationSummary>> _rows;

  @override
  void initState() {
    super.initState();
    _rows = widget.store.conversations(widget.profileId);
  }

  Future<void> _confirmDelete(
    ConversationSummary conversation,
    bool purgeMemory,
  ) async {
    final AppStrings strings = AppStrings.of(context);
    final bool pt = strings.locale.languageCode == 'pt';
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text(
          purgeMemory
              ? strings.deleteHistoryAndMemory
              : strings.deleteHistoryOnly,
        ),
        content: Text(
          purgeMemory
              ? (pt
                    ? 'As fontes desta conversa serão removidas, o ledger será reencadeado e o histórico será ocultado.'
                    : 'This conversation’s sources will be removed, the ledger will be rechained, and history will be hidden.')
              : (pt
                    ? 'O histórico será ocultado, mas as memórias autorizadas continuarão disponíveis na aba Memória.'
                    : 'History will be hidden, but authorized memories remain available in the Memory tab.'),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(strings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(pt ? 'Confirmar exclusão' : 'Confirm deletion'),
          ),
        ],
      ),
    );
    if (!mounted || confirmed != true) return;
    await widget.onDelete(conversation, purgeMemory);
    if (!mounted) return;
    setState(() => _rows = widget.store.conversations(widget.profileId));
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return Column(
      children: <Widget>[
        _SectionHeader(
          title: strings.history,
          action: TextButton.icon(
            onPressed: widget.onCreate,
            icon: const Icon(Icons.add, size: 18),
            label: Text(strings.newConversation),
          ),
        ),
        Expanded(
          child: FutureBuilder<List<ConversationSummary>>(
            future: _rows,
            builder:
                (
                  BuildContext context,
                  AsyncSnapshot<List<ConversationSummary>> snapshot,
                ) {
                  final List<ConversationSummary> rows =
                      snapshot.data ?? const <ConversationSummary>[];
                  if (snapshot.connectionState != ConnectionState.done) {
                    return const Center(
                      child: CircularProgressIndicator(strokeWidth: 1),
                    );
                  }
                  return ListView.separated(
                    padding: const EdgeInsets.all(20),
                    itemCount: rows.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (BuildContext context, int index) {
                      final ConversationSummary row = rows[index];
                      final bool active = row.id == widget.activeConversationId;
                      return ListTile(
                        onTap: () => widget.onOpen(row),
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 4,
                          vertical: 8,
                        ),
                        leading: Icon(
                          active
                              ? Icons.radio_button_checked
                              : Icons.radio_button_off,
                        ),
                        title: Text(row.title),
                        subtitle: Text(
                          '${row.updatedAt.toLocal()} · ${row.memoryMode.name}',
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 11,
                          ),
                        ),
                        trailing: PopupMenuButton<bool>(
                          tooltip: strings.historyDeleteOptions,
                          onSelected: (bool purge) =>
                              unawaited(_confirmDelete(row, purge)),
                          itemBuilder: (BuildContext context) =>
                              <PopupMenuEntry<bool>>[
                                PopupMenuItem(
                                  value: false,
                                  child: Text(strings.deleteHistoryOnly),
                                ),
                                PopupMenuItem(
                                  value: true,
                                  child: Text(strings.deleteHistoryAndMemory),
                                ),
                              ],
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

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.title, required this.action});

  final String title;
  final Widget action;

  @override
  Widget build(BuildContext context) => Container(
    height: 68,
    padding: const EdgeInsets.symmetric(horizontal: 24),
    decoration: const BoxDecoration(
      border: Border(bottom: BorderSide(color: ProofRayColors.hairline)),
    ),
    child: Row(
      children: <Widget>[
        Text(title, style: Theme.of(context).textTheme.headlineSmall),
        const Spacer(),
        action,
      ],
    ),
  );
}
