import 'package:flutter/material.dart';

import '../../design/proofray_theme.dart';
import '../../l10n/app_strings.dart';
import '../../models/chat_models.dart';

class OnboardingResult {
  const OnboardingResult({
    required this.displayName,
    required this.memoryMode,
    required this.locale,
    required this.timezone,
    this.providerKind,
    this.providerEndpoint,
    this.providerModelId,
    this.providerSecret,
    this.providerCustomModel = false,
    this.providerSupportsTools = true,
  });

  final String displayName;
  final MemoryMode memoryMode;
  final String locale;
  final String timezone;
  final String? providerKind;
  final String? providerEndpoint;
  final String? providerModelId;
  final String? providerSecret;
  final bool providerCustomModel;
  final bool providerSupportsTools;
}

class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({
    required this.onFinished,
    required this.databasePath,
    super.key,
  });

  final ValueChanged<OnboardingResult> onFinished;
  final String databasePath;

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  static const Map<String, String> _providerEndpoints = <String, String>{
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
    'openai': 'https://api.openai.com/v1',
    'anthropic': 'https://api.anthropic.com/v1',
    'openai_compatible': 'http://127.0.0.1:11434/v1',
  };
  final PageController _pages = PageController();
  final TextEditingController _name = TextEditingController();
  final TextEditingController _providerEndpoint = TextEditingController();
  final TextEditingController _providerModel = TextEditingController();
  final TextEditingController _providerSecret = TextEditingController();
  int _page = 0;
  MemoryMode _mode = MemoryMode.keywords;
  String _locale = 'system';
  String _timezone = 'system';
  String _providerKind = 'none';
  bool _providerCustomModel = false;
  bool _providerSupportsTools = true;
  String? _providerError;

  @override
  void dispose() {
    _pages.dispose();
    _name.dispose();
    _providerEndpoint.dispose();
    _providerModel.dispose();
    _providerSecret.dispose();
    super.dispose();
  }

  void _next() {
    if (_page == 3 &&
        _providerKind != 'none' &&
        (_providerEndpoint.text.trim().isEmpty ||
            _providerModel.text.trim().isEmpty)) {
      setState(() => _providerError = 'provider_fields_required');
      return;
    }
    if (_page < 4) {
      _pages.nextPage(
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOutCubic,
      );
      return;
    }
    widget.onFinished(
      OnboardingResult(
        displayName: _name.text.trim().isEmpty ? 'User' : _name.text.trim(),
        memoryMode: _mode,
        locale: _locale,
        timezone: _timezone,
        providerKind: _providerKind == 'none' ? null : _providerKind,
        providerEndpoint: _providerKind == 'none'
            ? null
            : _providerEndpoint.text.trim(),
        providerModelId: _providerKind == 'none'
            ? null
            : _providerModel.text.trim(),
        providerSecret: _providerSecret.text.trim().isEmpty
            ? null
            : _providerSecret.text.trim(),
        providerCustomModel: _providerCustomModel,
        providerSupportsTools: _providerSupportsTools,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bool pt = AppStrings.of(context).locale.languageCode == 'pt';
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(24, 20, 24, 4),
              child: Row(
                children: <Widget>[
                  Image.asset(
                    'assets/ProofRay.jpeg',
                    width: 38,
                    height: 38,
                    cacheWidth: 160,
                    cacheHeight: 160,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      'ProofRay',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '${_page + 1}/5',
                    style: Theme.of(context).textTheme.labelSmall,
                  ),
                ],
              ),
            ),
            const Divider(),
            Expanded(
              child: PageView(
                controller: _pages,
                onPageChanged: (int value) => setState(() => _page = value),
                children: <Widget>[
                  _OnboardingPage(
                    eyebrow: 'LOCAL / PROOF-FIRST',
                    title: pt
                        ? 'Memória que consegue mostrar por que lembra.'
                        : 'Memory that can show why it remembers.',
                    body: pt
                        ? 'Suas conversas ficam criptografadas neste dispositivo. O ProofRay responde quando consegue reabrir a fonte; caso contrário mostra evidência ou se abstém.'
                        : 'Your conversations remain encrypted on this device. ProofRay answers when it can reopen the source; otherwise it shows evidence or abstains.',
                    child: const _EncryptionStatement(),
                  ),
                  _OnboardingPage(
                    eyebrow: 'PROFILE',
                    title: pt ? 'Seu perfil local.' : 'Your local profile.',
                    body: pt
                        ? 'Nome, idioma e fuso ajudam a preservar autoria e datas sem enviar nada para a nuvem.'
                        : 'Name, language and timezone preserve authorship and dates without sending anything to the cloud.',
                    child: Column(
                      children: <Widget>[
                        TextField(
                          controller: _name,
                          decoration: InputDecoration(
                            labelText: pt
                                ? 'Como devemos chamar você?'
                                : 'What should we call you?',
                          ),
                        ),
                        const SizedBox(height: 12),
                        DropdownButtonFormField<String>(
                          initialValue: _locale,
                          isExpanded: true,
                          decoration: InputDecoration(
                            labelText: pt ? 'Idioma' : 'Language',
                          ),
                          items: const <DropdownMenuItem<String>>[
                            DropdownMenuItem(
                              value: 'system',
                              child: Text('System / Sistema'),
                            ),
                            DropdownMenuItem(
                              value: 'pt-BR',
                              child: Text('Português (Brasil)'),
                            ),
                            DropdownMenuItem(
                              value: 'en',
                              child: Text('English'),
                            ),
                          ],
                          onChanged: (String? value) =>
                              setState(() => _locale = value ?? 'system'),
                        ),
                        const SizedBox(height: 12),
                        DropdownButtonFormField<String>(
                          initialValue: _timezone,
                          isExpanded: true,
                          decoration: InputDecoration(
                            labelText: pt ? 'Fuso horário' : 'Timezone',
                          ),
                          items: const <DropdownMenuItem<String>>[
                            DropdownMenuItem(
                              value: 'system',
                              child: Text('System / Sistema'),
                            ),
                            DropdownMenuItem(
                              value: 'America/Sao_Paulo',
                              child: Text('America/Sao_Paulo'),
                            ),
                            DropdownMenuItem(value: 'UTC', child: Text('UTC')),
                          ],
                          onChanged: (String? value) =>
                              setState(() => _timezone = value ?? 'system'),
                        ),
                      ],
                    ),
                  ),
                  _OnboardingPage(
                    eyebrow: 'ACTIVATION',
                    title: pt
                        ? 'Quando a memória deve entrar na conversa?'
                        : 'When should memory enter the conversation?',
                    body: pt
                        ? 'Você pode trocar o modo a qualquer momento pelo cérebro ao lado do campo de mensagem.'
                        : 'You can change this at any time from the brain beside the composer.',
                    child: Column(
                      children: <Widget>[
                        _ModeChoice(
                          selected: _mode == MemoryMode.tool,
                          title: 'Tool call',
                          body: pt
                              ? 'A IA decide quando chamar o ProofRay. Requer um modelo com ferramentas.'
                              : 'The model decides when to call ProofRay. Requires tool support.',
                          onTap: () => setState(() => _mode = MemoryMode.tool),
                        ),
                        const SizedBox(height: 10),
                        _ModeChoice(
                          selected: _mode == MemoryMode.keywords,
                          title: pt ? 'Palavras-chave' : 'Keywords',
                          body: pt
                              ? 'Ativa com termos como lembra, recordar, remember e recall.'
                              : 'Activates on terms such as remember and recall.',
                          onTap: () =>
                              setState(() => _mode = MemoryMode.keywords),
                        ),
                      ],
                    ),
                  ),
                  _OnboardingPage(
                    eyebrow: 'OPTIONAL AI',
                    title: pt
                        ? 'Comece com ou sem uma IA.'
                        : 'Start with or without an AI.',
                    body: pt
                        ? 'Sem provedor, resposta determinística, evidência e abstenção continuam funcionando. Você pode conectar Gemini, OpenAI, Anthropic, Ollama ou LM Studio depois.'
                        : 'Without a provider, deterministic answers, evidence and abstention still work. Gemini, OpenAI, Anthropic, Ollama or LM Studio can be connected later.',
                    child: Column(
                      children: <Widget>[
                        DropdownButtonFormField<String>(
                          initialValue: _providerKind,
                          isExpanded: true,
                          decoration: InputDecoration(
                            labelText: pt
                                ? 'Provedor opcional'
                                : 'Optional provider',
                          ),
                          items: const <DropdownMenuItem<String>>[
                            DropdownMenuItem(
                              value: 'none',
                              child: Text('Sem IA / No AI'),
                            ),
                            DropdownMenuItem(
                              value: 'gemini',
                              child: Text('Gemini'),
                            ),
                            DropdownMenuItem(
                              value: 'openai',
                              child: Text('OpenAI'),
                            ),
                            DropdownMenuItem(
                              value: 'anthropic',
                              child: Text('Anthropic'),
                            ),
                            DropdownMenuItem(
                              value: 'openai_compatible',
                              child: Text(
                                'OpenAI compatible / Ollama / LM Studio',
                              ),
                            ),
                          ],
                          onChanged: (String? value) {
                            if (value == null) return;
                            setState(() {
                              _providerKind = value;
                              _providerError = null;
                              _providerEndpoint.text =
                                  _providerEndpoints[value] ?? '';
                            });
                          },
                        ),
                        if (_providerKind != 'none') ...<Widget>[
                          const SizedBox(height: 10),
                          TextField(
                            controller: _providerEndpoint,
                            decoration: const InputDecoration(
                              labelText: 'Endpoint',
                            ),
                          ),
                          const SizedBox(height: 10),
                          TextField(
                            controller: _providerModel,
                            decoration: InputDecoration(
                              labelText: pt ? 'ID do modelo' : 'Model ID',
                            ),
                          ),
                          const SizedBox(height: 10),
                          TextField(
                            controller: _providerSecret,
                            obscureText: true,
                            enableSuggestions: false,
                            autocorrect: false,
                            decoration: InputDecoration(
                              labelText: pt
                                  ? 'Chave de API (cofre ou sessão)'
                                  : 'API key (vault or session)',
                            ),
                          ),
                          SwitchListTile(
                            contentPadding: EdgeInsets.zero,
                            title: Text(
                              pt
                                  ? 'ID personalizado ou experimental'
                                  : 'Custom or experimental model ID',
                            ),
                            value: _providerCustomModel,
                            onChanged: (bool value) =>
                                setState(() => _providerCustomModel = value),
                          ),
                          SwitchListTile(
                            contentPadding: EdgeInsets.zero,
                            title: Text(
                              pt
                                  ? 'O modelo suporta tool calling'
                                  : 'Model supports tool calling',
                            ),
                            value: _providerSupportsTools,
                            onChanged: (bool value) =>
                                setState(() => _providerSupportsTools = value),
                          ),
                        ] else
                          Row(
                            children: <Widget>[
                              const Icon(Icons.offline_bolt_outlined),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Text(
                                  pt ? 'Nenhuma conta ou rede é necessária.' : 'No account and no network are required.',
                                ),
                              ),
                            ],
                          ),
                        if (_providerError != null) ...<Widget>[
                          const SizedBox(height: 8),
                          Text(
                            pt
                                ? 'Informe endpoint e ID do modelo, ou escolha Sem IA.'
                                : 'Enter endpoint and model ID, or choose No AI.',
                            style: const TextStyle(color: ProofRayColors.ink),
                          ),
                        ],
                      ],
                    ),
                  ),
                  _OnboardingPage(
                    eyebrow: 'LOCAL DATABASE',
                    title: pt
                        ? 'O banco local será criado automaticamente.'
                        : 'Your local database will be created automatically.',
                    body: pt
                        ? 'Você pode começar sem API de IA. Gemini, OpenAI, Anthropic e endpoints compatíveis podem ser conectados depois em Configurações.'
                        : 'You can start without an AI API. Gemini, OpenAI, Anthropic and compatible endpoints can be connected later in Settings.',
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        const _EncryptionStatement(),
                        const SizedBox(height: 14),
                        SelectableText(
                          widget.databasePath,
                          style: const TextStyle(
                            fontFamily: 'monospace',
                            fontSize: 11,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(20),
              child: SizedBox(
                width: double.infinity,
                height: 48,
                child: FilledButton(
                  onPressed: _next,
                  style: FilledButton.styleFrom(
                    shape: const RoundedRectangleBorder(
                      borderRadius: BorderRadius.all(Radius.circular(2)),
                    ),
                  ),
                  child: Text(
                    _page == 4
                        ? (pt ? 'Criar memória local' : 'Create local memory')
                        : (pt ? 'Continuar' : 'Continue'),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _OnboardingPage extends StatelessWidget {
  const _OnboardingPage({
    required this.eyebrow,
    required this.title,
    required this.body,
    required this.child,
  });

  final String eyebrow;
  final String title;
  final String body;
  final Widget child;

  @override
  Widget build(BuildContext context) => Center(
    child: SingleChildScrollView(
      padding: const EdgeInsets.all(28),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 580),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(eyebrow, style: Theme.of(context).textTheme.labelSmall),
            const SizedBox(height: 18),
            Text(title, style: Theme.of(context).textTheme.headlineLarge),
            const SizedBox(height: 18),
            Text(
              body,
              style: const TextStyle(
                height: 1.6,
                color: ProofRayColors.quietInk,
              ),
            ),
            const SizedBox(height: 34),
            child,
          ],
        ),
      ),
    ),
  );
}

class _ModeChoice extends StatelessWidget {
  const _ModeChoice({
    required this.selected,
    required this.title,
    required this.body,
    required this.onTap,
  });

  final bool selected;
  final String title;
  final String body;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
    onTap: onTap,
    borderRadius: const BorderRadius.all(Radius.circular(2)),
    child: Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        border: Border.all(
          color: selected ? ProofRayColors.ink : ProofRayColors.hairline,
        ),
        borderRadius: const BorderRadius.all(Radius.circular(2)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(selected ? Icons.radio_button_checked : Icons.radio_button_off),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(title, style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 5),
                Text(
                  body,
                  style: const TextStyle(color: ProofRayColors.quietInk),
                ),
              ],
            ),
          ),
        ],
      ),
    ),
  );
}

class _EncryptionStatement extends StatelessWidget {
  const _EncryptionStatement();

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(16),
    decoration: const BoxDecoration(
      border: Border.fromBorderSide(BorderSide(color: ProofRayColors.ink)),
    ),
    child: const Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Icon(Icons.lock_outline),
        SizedBox(width: 12),
        Expanded(
          child: Text(
            'SQLCipher · 256-bit key · no plaintext fallback',
            style: TextStyle(fontFamily: 'monospace', fontSize: 12),
          ),
        ),
      ],
    ),
  );
}
