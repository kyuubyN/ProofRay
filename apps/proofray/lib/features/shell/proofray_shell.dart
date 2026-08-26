import 'dart:async';

import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../features/chat/chat_controller.dart';
import '../../features/local_models/local_model_controller.dart';
import '../../features/chat/chat_screen.dart';
import '../../features/history/delete_conversation_prompt.dart';
import '../../features/history/history_screen.dart';
import '../../features/memory/memory_screen.dart';
import '../../features/settings/settings_screen.dart';
import '../../features/sources/sources_screen.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';
import '../../storage/conversation_store.dart';
import '../../storage/integration_store.dart';

class ProofRayShell extends StatefulWidget {
  const ProofRayShell({
    required this.chatController,
    required this.localModels,
    required this.providerSwitcher,
    required this.onProviderSelected,
    required this.store,
    required this.integrations,
    required this.profileId,
    required this.onOpenConversation,
    required this.onNewConversation,
    required this.onDeleteConversation,
    required this.locale,
    required this.onLocaleChanged,
    super.key,
  });

  final ChatController chatController;
  final LocalModelController localModels;
  final Widget providerSwitcher;
  final Future<void> Function(String? providerId, {bool supportsTools})
  onProviderSelected;
  final ConversationStore store;
  final IntegrationStore integrations;
  final String profileId;
  final Future<void> Function(ConversationSummary) onOpenConversation;
  final Future<void> Function() onNewConversation;
  final Future<void> Function(
    ConversationSummary conversation,
    bool purgeMemory,
  )
  onDeleteConversation;
  final Locale locale;
  final ValueChanged<Locale> onLocaleChanged;

  @override
  State<ProofRayShell> createState() => _ProofRayShellState();
}

class _ProofRayShellState extends State<ProofRayShell> {
  // Selection is held as an identity rather than an index because the two
  // layouts no longer offer the same panes: History exists only on mobile,
  // where the desktop sidebar that replaced it has nowhere to live. With a
  // shared index, resizing the window across the breakpoint would silently
  // land on a different pane than the one that was open.
  _Pane _pane = _Pane.chat;
  bool _historyCollapsed = false;
  final GlobalKey<_DesktopHistoryState> _desktopHistoryKey =
      GlobalKey<_DesktopHistoryState>();

  Future<void> _openDesktopConversation(ConversationSummary summary) async {
    await widget.onOpenConversation(summary);
    _desktopHistoryKey.currentState?.refresh();
  }

  Future<void> _createDesktopConversation() async {
    await widget.onNewConversation();
    _desktopHistoryKey.currentState?.refresh();
  }

  Future<void> _deleteDesktopConversation(
    ConversationSummary conversation,
    bool purgeMemory,
  ) async {
    await widget.onDeleteConversation(conversation, purgeMemory);
    _desktopHistoryKey.currentState?.refresh();
  }

  @override
  Widget build(BuildContext context) {
    final bool desktop = MediaQuery.sizeOf(context).width >= 820;
    final AppStrings strings = AppStrings.of(context);
    final List<_Destination> destinations = _destinations(context, desktop);
    // A pane can disappear under the selection when the window crosses the
    // breakpoint; falling back to the chat is the only pane both layouts share.
    final _Pane pane =
        destinations.any((_Destination item) => item.pane == _pane)
        ? _pane
        : _Pane.chat;
    final Widget content = switch (pane) {
      _Pane.chat => KeyedSubtree(
        key: ValueKey<String>(
          'proofray-conversation-${widget.chatController.conversationId}',
        ),
        child: ChatScreen(
          controller: widget.chatController,
          localModels: widget.localModels,
          providerSwitcher: widget.providerSwitcher,
        ),
      ),
      _Pane.history => HistoryScreen(
        store: widget.store,
        profileId: widget.profileId,
        activeConversationId: widget.chatController.conversationId,
        onOpen: (ConversationSummary summary) =>
            unawaited(widget.onOpenConversation(summary)),
        onCreate: widget.onNewConversation,
        onDelete: widget.onDeleteConversation,
      ),
      _Pane.memory => MemoryScreen(
        store: widget.store,
        bridge: () => widget.chatController.bridge,
      ),
      _Pane.sources => SourcesScreen(
        integrations: widget.integrations,
        bridge: () => widget.chatController.bridge,
        currentLocale: widget.locale,
        store: widget.store,
      ),
      _Pane.settings => SettingsScreen(
        integrations: widget.integrations,
        localModels: widget.localModels,
        onProviderSelected: widget.onProviderSelected,
        bridge: () => widget.chatController.bridge,
        chat: widget.chatController,
        currentLocale: widget.locale,
        onLocaleChanged: widget.onLocaleChanged,
        store: widget.store,
        profileId: widget.profileId,
      ),
    };

    if (!desktop) {
      return Scaffold(
        appBar: AppBar(
          title: const Text('ProofRay'),
          centerTitle: false,
          surfaceTintColor: Colors.transparent,
          shape: const Border(
            bottom: BorderSide(color: ProofRayColors.hairline),
          ),
        ),
        body: content,
        bottomNavigationBar: NavigationBar(
          selectedIndex: destinations.indexWhere(
            (_Destination item) => item.pane == pane,
          ),
          onDestinationSelected: (int value) =>
              setState(() => _pane = destinations[value].pane),
          indicatorColor: ProofRayColors.softPaper,
          // Bit Horizon is monochrome: the round indicator behind the
          // active destination is the only thing that changes, never the
          // icon's own color -- an active icon must never render white
          // on the paper-colored background it sits on.
          destinations: destinations
              .map(
                (_Destination item) => NavigationDestination(
                  icon: Icon(item.icon, color: ProofRayColors.ink),
                  selectedIcon: Icon(
                    item.selectedIcon,
                    color: ProofRayColors.ink,
                  ),
                  label: item.label,
                ),
              )
              .toList(growable: false),
        ),
      );
    }

    return Scaffold(
      body: Row(
        children: <Widget>[
          NavigationRail(
            selectedIndex: destinations.indexWhere(
              (_Destination item) => item.pane == pane,
            ),
            onDestinationSelected: (int value) =>
                setState(() => _pane = destinations[value].pane),
            backgroundColor: ProofRayColors.softPaper,
            indicatorColor: ProofRayColors.paper,
            labelType: NavigationRailLabelType.all,
            leading: Padding(
              padding: const EdgeInsets.only(bottom: 20),
              child: Image.asset(
                'assets/ProofRay.jpeg',
                width: 42,
                height: 42,
                cacheWidth: 160,
                cacheHeight: 160,
              ),
            ),
            // Same monochrome rule as the mobile NavigationBar above: the
            // round indicator is the only thing that changes on selection.
            destinations: destinations
                .map(
                  (_Destination item) => NavigationRailDestination(
                    icon: Icon(item.icon, color: ProofRayColors.ink),
                    selectedIcon: Icon(
                      item.selectedIcon,
                      color: ProofRayColors.ink,
                    ),
                    label: Text(item.label),
                  ),
                )
                .toList(growable: false),
          ),
          const VerticalDivider(width: 1),
          if (pane == _Pane.chat) ...<Widget>[
            if (_historyCollapsed)
              _SidebarCollapseStrip(
                tooltip: strings.locale.languageCode == 'pt'
                    ? 'Expandir histórico'
                    : 'Expand history',
                icon: Icons.chevron_right,
                onPressed: () => setState(() => _historyCollapsed = false),
              )
            else
              SizedBox(
                width: 248,
                child: _DesktopHistory(
                  key: _desktopHistoryKey,
                  store: widget.store,
                  profileId: widget.profileId,
                  activeConversationId: widget.chatController.conversationId,
                  onDelete: _deleteDesktopConversation,
                  onOpen: (ConversationSummary summary) =>
                      unawaited(_openDesktopConversation(summary)),
                  onCreate: () => unawaited(_createDesktopConversation()),
                  onCollapse: () => setState(() => _historyCollapsed = true),
                ),
              ),
            const VerticalDivider(width: 1),
          ],
          Expanded(child: content),
        ],
      ),
    );
  }

  List<_Destination> _destinations(BuildContext context, bool desktop) {
    final AppStrings strings = AppStrings.of(context);
    return <_Destination>[
      _Destination(
        _Pane.chat,
        strings.chat,
        Icons.chat_bubble_outline,
        Icons.chat_bubble,
      ),
      // Desktop keeps its conversations in the sidebar beside the chat, where
      // opening, renaming and deleting one no longer costs a trip to another
      // pane. Mobile has no room for that rail, so History stays its only way
      // to reach them at all.
      if (!desktop)
        _Destination(
          _Pane.history,
          strings.history,
          Icons.schedule_outlined,
          Icons.schedule,
        ),
      _Destination(
        _Pane.memory,
        strings.memory,
        Icons.blur_on_outlined,
        Icons.blur_on,
      ),
      _Destination(_Pane.sources, strings.sources, Icons.hub_outlined, Icons.hub),
      _Destination(
        _Pane.settings,
        strings.settings,
        Icons.tune_outlined,
        Icons.tune,
      ),
    ];
  }
}

class _DesktopHistory extends StatefulWidget {
  const _DesktopHistory({
    required this.store,
    required this.profileId,
    required this.activeConversationId,
    required this.onOpen,
    required this.onCreate,
    required this.onCollapse,
    required this.onDelete,
    super.key,
  });

  final ConversationStore store;
  final String profileId;
  final String activeConversationId;
  final ValueChanged<ConversationSummary> onOpen;
  final VoidCallback onCreate;
  final VoidCallback onCollapse;
  final Future<void> Function(
    ConversationSummary conversation,
    bool purgeMemory,
  )
  onDelete;

  @override
  State<_DesktopHistory> createState() => _DesktopHistoryState();
}

class _DesktopHistoryState extends State<_DesktopHistory> {
  late Future<List<ConversationSummary>> _rows;

  @override
  void initState() {
    super.initState();
    _rows = widget.store.conversations(widget.profileId);
  }

  void refresh() {
    if (!mounted) return;
    // Block body, never `setState(() => _rows = ...)`: the arrow form returns
    // the assigned Future, and Flutter rejects a setState callback that
    // returns one -- it throws an unhandled exception mid-rebuild instead of
    // refreshing the list. Observed in a real run every time a conversation
    // was created from the desktop sidebar.
    setState(() {
      _rows = widget.store.conversations(widget.profileId);
    });
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
    if (!mounted ||
        title == null ||
        title.isEmpty ||
        title == conversation.title) {
      return;
    }
    await widget.store.renameConversation(conversation.id, title);
    if (!mounted) return;
    // Block body, never `setState(() => _rows = ...)`: the arrow form returns
    // the assigned Future, and Flutter rejects a setState callback that
    // returns one -- it throws an unhandled exception mid-rebuild instead of
    // refreshing the list. Observed in a real run every time a conversation
    // was created from the desktop sidebar.
    setState(() {
      _rows = widget.store.conversations(widget.profileId);
    });
  }

  @override
  Widget build(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return ColoredBox(
      color: ProofRayColors.softPaper,
      child: Column(
        children: <Widget>[
          SizedBox(
            height: 68,
            child: Row(
              children: <Widget>[
                const SizedBox(width: 16),
                Text(
                  strings.history.toUpperCase(),
                  style: Theme.of(context).textTheme.labelSmall,
                ),
                const Spacer(),
                IconButton(
                  onPressed: widget.onCreate,
                  icon: const Icon(Icons.add, size: 18),
                ),
                IconButton(
                  tooltip: strings.locale.languageCode == 'pt'
                      ? 'Recolher histórico'
                      : 'Collapse history',
                  onPressed: widget.onCollapse,
                  icon: const Icon(Icons.chevron_left, size: 18),
                ),
                const SizedBox(width: 6),
              ],
            ),
          ),
          const Divider(),
          Expanded(
            child: FutureBuilder<List<ConversationSummary>>(
              future: _rows,
              builder:
                  (
                    BuildContext context,
                    AsyncSnapshot<List<ConversationSummary>> snapshot,
                  ) => ListView(
                    children: <Widget>[
                      for (final ConversationSummary row
                          in snapshot.data ?? const <ConversationSummary>[])
                        Material(
                          color: Colors.transparent,
                          child: ListTile(
                            dense: true,
                            selected: row.id == widget.activeConversationId,
                            selectedColor: ProofRayColors.ink,
                            title: Text(
                              row.title,
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            subtitle: Text(
                              row.memoryMode.name,
                              style: const TextStyle(
                                fontFamily: 'monospace',
                                fontSize: 9,
                              ),
                            ),
                            // Deleting from here is the point: having to leave
                            // the conversation for a separate History tab just
                            // to drop one thread is exactly the detour this
                            // avoids.
                            trailing: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: <Widget>[
                                IconButton(
                                  tooltip: strings.locale.languageCode == 'pt'
                                      ? 'Renomear conversa'
                                      : 'Rename conversation',
                                  onPressed: () => unawaited(_rename(row)),
                                  visualDensity: VisualDensity.compact,
                                  padding: EdgeInsets.zero,
                                  constraints: const BoxConstraints(
                                    minWidth: 30,
                                    minHeight: 30,
                                  ),
                                  icon: const Icon(
                                    Icons.edit_outlined,
                                    size: 16,
                                  ),
                                ),
                                DeleteConversationButton(
                                  conversation: row,
                                  onDelete: widget.onDelete,
                                ),
                              ],
                            ),
                            onTap: () => widget.onOpen(row),
                          ),
                        ),
                    ],
                  ),
            ),
          ),
        ],
      ),
    );
  }
}

enum _Pane { chat, history, memory, sources, settings }

class _Destination {
  const _Destination(this.pane, this.label, this.icon, this.selectedIcon);

  final _Pane pane;
  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

/// The thin, always-visible strip a collapsed desktop sidebar shrinks down
/// to -- just enough room to re-expand it, so collapsing a panel never
/// leaves someone without a way back.
class _SidebarCollapseStrip extends StatelessWidget {
  const _SidebarCollapseStrip({
    required this.tooltip,
    required this.icon,
    required this.onPressed,
  });

  final String tooltip;
  final IconData icon;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => ColoredBox(
    color: ProofRayColors.softPaper,
    child: SizedBox(
      width: 32,
      child: IconButton(
        tooltip: tooltip,
        onPressed: onPressed,
        icon: Icon(icon, size: 16, color: ProofRayColors.ink),
      ),
    ),
  );
}
