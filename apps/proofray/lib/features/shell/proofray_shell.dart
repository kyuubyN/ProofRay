import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../features/chat/chat_controller.dart';
import '../../features/chat/chat_screen.dart';
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
  final ConversationStore store;
  final IntegrationStore integrations;
  final String profileId;
  final ValueChanged<ConversationSummary> onOpenConversation;
  final VoidCallback onNewConversation;
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
  int _destination = 0;

  @override
  Widget build(BuildContext context) {
    final bool desktop = MediaQuery.sizeOf(context).width >= 820;
    final List<_Destination> destinations = _destinations(context);
    final Widget content = switch (_destination) {
      0 => ChatScreen(controller: widget.chatController),
      1 => HistoryScreen(
        store: widget.store,
        profileId: widget.profileId,
        activeConversationId: widget.chatController.conversationId,
        onOpen: widget.onOpenConversation,
        onCreate: widget.onNewConversation,
        onDelete: widget.onDeleteConversation,
      ),
      2 => MemoryScreen(
        store: widget.store,
        bridge: () => widget.chatController.bridge,
      ),
      3 => SourcesScreen(
        integrations: widget.integrations,
        bridge: () => widget.chatController.bridge,
        currentLocale: widget.locale,
        store: widget.store,
      ),
      _ => SettingsScreen(
        integrations: widget.integrations,
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
          selectedIndex: _destination,
          onDestinationSelected: (int value) =>
              setState(() => _destination = value),
          indicatorColor: ProofRayColors.softPaper,
          destinations: destinations
              .map(
                (_Destination item) => NavigationDestination(
                  icon: Icon(item.icon),
                  selectedIcon: Icon(item.selectedIcon),
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
            selectedIndex: _destination,
            onDestinationSelected: (int value) =>
                setState(() => _destination = value),
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
            destinations: destinations
                .map(
                  (_Destination item) => NavigationRailDestination(
                    icon: Icon(item.icon),
                    selectedIcon: Icon(item.selectedIcon),
                    label: Text(item.label),
                  ),
                )
                .toList(growable: false),
          ),
          const VerticalDivider(width: 1),
          if (_destination == 0) ...<Widget>[
            SizedBox(
              width: 248,
              child: _DesktopHistory(
                store: widget.store,
                profileId: widget.profileId,
                activeConversationId: widget.chatController.conversationId,
                onOpen: widget.onOpenConversation,
                onCreate: widget.onNewConversation,
              ),
            ),
            const VerticalDivider(width: 1),
          ],
          Expanded(child: content),
        ],
      ),
    );
  }

  List<_Destination> _destinations(BuildContext context) {
    final AppStrings strings = AppStrings.of(context);
    return <_Destination>[
      _Destination(strings.chat, Icons.chat_bubble_outline, Icons.chat_bubble),
      _Destination(strings.history, Icons.schedule_outlined, Icons.schedule),
      _Destination(strings.memory, Icons.blur_on_outlined, Icons.blur_on),
      _Destination(strings.sources, Icons.hub_outlined, Icons.hub),
      _Destination(strings.settings, Icons.tune_outlined, Icons.tune),
    ];
  }
}

class _DesktopHistory extends StatelessWidget {
  const _DesktopHistory({
    required this.store,
    required this.profileId,
    required this.activeConversationId,
    required this.onOpen,
    required this.onCreate,
  });

  final ConversationStore store;
  final String profileId;
  final String activeConversationId;
  final ValueChanged<ConversationSummary> onOpen;
  final VoidCallback onCreate;

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
                  onPressed: onCreate,
                  icon: const Icon(Icons.add, size: 18),
                ),
                const SizedBox(width: 6),
              ],
            ),
          ),
          const Divider(),
          Expanded(
            child: FutureBuilder<List<ConversationSummary>>(
              future: store.conversations(profileId),
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
                            selected: row.id == activeConversationId,
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
                            onTap: () => onOpen(row),
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

class _Destination {
  const _Destination(this.label, this.icon, this.selectedIcon);

  final String label;
  final IconData icon;
  final IconData selectedIcon;
}
