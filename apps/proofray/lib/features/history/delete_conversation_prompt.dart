import 'package:flutter/material.dart';

import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';

/// Asks whether a conversation should also give up its authorized memory.
///
/// Shared by the History tab and the desktop chat sidebar so the two can never
/// drift into describing the same destructive action differently. Deleting a
/// conversation is not the same act as forgetting what was said in it, and the
/// difference has to be stated before either happens.
Future<bool> confirmConversationDeletion(
  BuildContext context, {
  required bool purgeMemory,
}) async {
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
  return confirmed == true;
}

/// The delete control itself: one target, both scopes.
///
/// A narrow sidebar has no room for two separate buttons, and offering only
/// "delete" would force a choice about memory that was never asked.
class DeleteConversationButton extends StatelessWidget {
  const DeleteConversationButton({
    required this.conversation,
    required this.onDelete,
    super.key,
    this.iconSize = 16,
  });

  final ConversationSummary conversation;
  final Future<void> Function(ConversationSummary conversation, bool purgeMemory)
  onDelete;
  final double iconSize;

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return PopupMenuButton<bool>(
      tooltip: strings.historyDeleteOptions,
      icon: Icon(Icons.delete_outline, size: iconSize),
      padding: EdgeInsets.zero,
      onSelected: (bool purge) async {
        if (!await confirmConversationDeletion(context, purgeMemory: purge)) {
          return;
        }
        await onDelete(conversation, purge);
      },
      itemBuilder: (BuildContext context) => <PopupMenuEntry<bool>>[
        PopupMenuItem<bool>(
          value: false,
          child: Text(strings.deleteHistoryOnly),
        ),
        PopupMenuItem<bool>(
          value: true,
          child: Text(strings.deleteHistoryAndMemory),
        ),
      ],
    );
  }
}
