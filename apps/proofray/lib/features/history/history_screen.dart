import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import 'delete_conversation_prompt.dart';
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
  // Awaitable so the list can refresh only once the conversation actually
  // exists. A plain VoidCallback left this screen showing a stale list until
  // something else happened to rebuild it.
  final Future<void> Function() onCreate;
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

  @override
  void didUpdateWidget(HistoryScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Opening a conversation elsewhere changes which row is active and can add
    // rows this screen already read; without this the list keeps showing the
    // snapshot taken the first time it was mounted.
    if (oldWidget.activeConversationId != widget.activeConversationId) {
      _refresh();
    }
  }

  void _refresh() {
    // Block body, never `setState(() => _rows = ...)`: the arrow form returns
    // the assigned Future, and Flutter rejects a setState callback that returns
    // one -- it throws mid-rebuild instead of refreshing. `void` is erased at
    // runtime, so a declared return type does not protect the call either.
    setState(() {
      _rows = widget.store.conversations(widget.profileId);
    });
  }

  Future<void> _create() async {
    await widget.onCreate();
    if (!mounted) return;
    _refresh();
  }

  Future<void> _confirmDelete(
    ConversationSummary conversation,
    bool purgeMemory,
  ) async {
    if (!await confirmConversationDeletion(context, purgeMemory: purgeMemory)) {
      return;
    }
    await widget.onDelete(conversation, purgeMemory);
    if (!mounted) return;
    _refresh();
  }

  Future<void> _rename(ConversationSummary conversation) async {
    final AppStrings strings = AppStrings.of(context);
    final bool pt = strings.locale.languageCode == 'pt';
    final TextEditingController controller = TextEditingController(
      text: conversation.title,
    );
    final String? title = await showDialog<String>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: Text(pt ? 'Renomear conversa' : 'Rename conversation'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 200,
          onSubmitted: (String value) => Navigator.pop(context, value.trim()),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(strings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: Text(pt ? 'Salvar' : 'Save'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (!mounted || title == null || title.isEmpty || title == conversation.title) {
      return;
    }
    await widget.store.renameConversation(conversation.id, title);
    if (!mounted) return;
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return Column(
      children: <Widget>[
        _SectionHeader(
          title: strings.history,
          action: TextButton.icon(
            onPressed: () => unawaited(_create()),
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
                        trailing: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: <Widget>[
                            IconButton(
                              tooltip: strings.locale.languageCode == 'pt'
                                  ? 'Renomear conversa'
                                  : 'Rename conversation',
                              onPressed: () => unawaited(_rename(row)),
                              icon: const Icon(Icons.edit_outlined, size: 18),
                            ),
                            PopupMenuButton<bool>(
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
                                      child: Text(
                                        strings.deleteHistoryAndMemory,
                                      ),
                                    ),
                                  ],
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
        // Expanded rather than a Spacer: on a narrow phone the title and the
        // action together are wider than the row, and a Spacer resolves that
        // by pushing the action off the edge. Letting the title give up its
        // own space keeps the button reachable at any width.
        Expanded(
          child: Text(
            title,
            style: Theme.of(context).textTheme.headlineSmall,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        const SizedBox(width: 8),
        Flexible(child: action),
      ],
    ),
  );
}
