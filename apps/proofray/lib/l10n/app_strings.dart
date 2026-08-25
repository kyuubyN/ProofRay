import 'package:flutter/widgets.dart';

class AppStrings {
  const AppStrings(this.locale);

  final Locale locale;

  bool get _pt => locale.languageCode.toLowerCase() == 'pt';

  static AppStrings of(BuildContext context) =>
      AppStrings(Localizations.localeOf(context));

  String get appName => 'ProofRay';
  String get chat => _pt ? 'Chat' : 'Chat';
  String get history => _pt ? 'Histórico' : 'History';
  String get memory => _pt ? 'Memória' : 'Memory';
  String get sources => _pt ? 'Fontes' : 'Sources';
  String get settings => _pt ? 'Configurações' : 'Settings';
  String get observatory => _pt ? 'Observatório' : 'Observatory';
  String get newConversation => _pt ? 'Nova conversa' : 'New conversation';
  String get askAnything => _pt ? 'Pergunte qualquer coisa…' : 'Ask anything…';
  String get memoryActivated =>
      _pt ? 'ProofRay consultou a memória' : 'ProofRay consulted memory';
  String get noMemoryConsult =>
      _pt ? 'A memória não foi consultada' : 'Memory was not consulted';
  String get proved => _pt ? 'PROVADA' : 'PROVED';
  String get evidence => _pt ? 'EVIDÊNCIA' : 'EVIDENCE';
  String get abstention => _pt ? 'ABSTENÇÃO' : 'ABSTENTION';
  String get contested => _pt ? 'CONFLITO' : 'CONTESTED';
  String get model => _pt ? 'RESPOSTA DA IA' : 'MODEL ANSWER';
  String get pending => _pt ? 'PROCESSANDO' : 'PROCESSING';
  String get toolMode => _pt ? 'Tool call' : 'Tool call';
  String get keywordMode => _pt ? 'Palavras-chave' : 'Keywords';
  String get forceNext => _pt ? 'Forçar próxima consulta' : 'Force next recall';
  String get memoryOff => _pt ? 'Memória desligada' : 'Memory off';
  String get exactCertifiedText =>
      _pt ? 'Texto certificado exato' : 'Exact certified text';
  String get proofSources => _pt ? 'Fontes verificadas' : 'Verified sources';
  String get noConversationYet => _pt
      ? 'Comece uma conversa. A memória continua local neste dispositivo.'
      : 'Start a conversation. Memory stays local on this device.';
  String get historyDeleteOptions =>
      _pt ? 'Opções de exclusão' : 'Delete options';
  String get deleteHistoryOnly =>
      _pt ? 'Apagar apenas o histórico' : 'Delete history only';
  String get deleteHistoryAndMemory =>
      _pt ? 'Apagar histórico e memória' : 'Delete history and memory';
  String get authorizedMemory =>
      _pt ? 'Memória autorizada' : 'Authorized memory';
  String get noAuthorizedMemory => _pt
      ? 'Ainda não há observações atestadas pelo usuário.'
      : 'No user-attested observations yet.';
  String get removeFromMemory =>
      _pt ? 'Remover da memória' : 'Remove from memory';
  String get removeAuthorizedMemory =>
      _pt ? 'Remover memória autorizada?' : 'Remove authorized memory?';
  String get removeMemoryExplanation => _pt
      ? 'O histórico continuará visível. A fonte será removida do ledger de prova e o campo restante será revalidado.'
      : 'The history remains visible. The source is removed from the proof ledger and the remaining field is revalidated.';
  String get cancel => _pt ? 'Cancelar' : 'Cancel';
  String get remove => _pt ? 'Remover' : 'Remove';
  String get aiProvider => _pt ? 'Provedor de IA' : 'AI provider';
  String get aiProviderExplanation => _pt
      ? 'Opcional. O modelo pode redigir ou chamar o ProofRay, mas nunca se torna autoridade de memória.'
      : 'Optional. The model can write or call ProofRay, but never becomes memory authority.';
  String get interfaceLanguage =>
      _pt ? 'Idioma da interface' : 'Interface language';
  String get provider => _pt ? 'Provedor' : 'Provider';
  String get apiKeyVaultOnly => _pt
      ? 'Chave de API (cofre ou somente esta sessão)'
      : 'API key (vault or this session only)';
  String get modelId => _pt ? 'ID do modelo' : 'Model ID';
  String get customModel =>
      _pt ? 'ID personalizado ou mais recente' : 'Custom / newer model ID';
  String get customModelExplanation => _pt
      ? 'Necessário para identificadores preview, latest ou experimentais.'
      : 'Required for preview, latest or experimental identifiers.';
  String get testAndSave => _pt ? 'Testar e salvar' : 'Test and save';
  String get discoverModels => _pt ? 'Descobrir modelos' : 'Discover models';
  String get useWithoutAi => _pt ? 'Usar sem IA' : 'Use without AI';
  String get discoveredModels =>
      _pt ? 'Modelos descobertos' : 'Discovered models';
  String get yes => _pt ? 'sim' : 'yes';
  String get no => _pt ? 'não' : 'no';
  String get memoryActivation =>
      _pt ? 'Ativação da memória' : 'Memory activation';
  String get keywordsCommaSeparated => _pt
      ? 'Palavras-chave (separadas por vírgula)'
      : 'Keywords (comma-separated)';
  String get saveDeterministicTrigger =>
      _pt ? 'Salvar gatilho determinístico' : 'Save deterministic trigger';
  String get savedLocally => _pt ? 'Salvos localmente' : 'Saved locally';
  String get localProfile => _pt ? 'Perfil local' : 'Local profile';
  String get displayName => _pt ? 'Nome' : 'Display name';
  String get timezone => _pt ? 'Fuso horário IANA' : 'IANA timezone';
  String get saveProfile => _pt ? 'Salvar perfil local' : 'Save local profile';
  String get sourcesIntroduction => _pt
      ? 'Bancos existentes são somente leitura. A importação confirma lotes de 256; namespaces gerenciados exigem uma ação separada.'
      : 'Existing databases are read-only. Import commits in batches of 256; managed namespaces require a separate action.';
  String get importTextFiles =>
      _pt ? 'Importar TXT, Markdown ou JSON' : 'Import TXT, Markdown or JSON';
  String get databaseEndpoint =>
      _pt ? 'URL do banco ou caminho local' : 'Database URL or local path';
  String get sourceType => _pt
      ? 'Tipo (detectado automaticamente quando inequívoco)'
      : 'Type (auto-detected when unambiguous)';
  String get credentialLease => _pt
      ? 'Credencial (cofre ou somente esta sessão)'
      : 'Credential (vault or this session only)';
  String get connectorOptions => _pt
      ? 'Opções JSON (banco, região, tabelas e flags de leitura)'
      : 'Options JSON (database, region, tables, read-only flags)';
  String get detectTestDiscover =>
      _pt ? 'Detectar, testar e descobrir' : 'Detect, test and discover';
  String get createManagedNamespace => _pt
      ? 'Criar namespace gerenciado dedicado'
      : 'Create dedicated managed namespace';
  String get namespaces => 'Namespaces';
  String get generatePreview => _pt ? 'Gerar prévia' : 'Generate preview';
  String get importAuthorizedMapping => _pt
      ? 'Importar o mapping autorizado da prévia'
      : 'Import authorized preview mapping';
  String get configuredSources =>
      _pt ? 'Fontes configuradas' : 'Configured sources';
  String get disconnectOnly => _pt ? 'Apenas desconectar' : 'Disconnect only';
  String get deleteImportedMemory =>
      _pt ? 'Apagar memória importada' : 'Delete imported memory';
  String get unlockLocalMemory =>
      _pt ? 'Desbloquear memória local' : 'Unlock local memory';
  String get vaultUnavailable => _pt
      ? 'O cofre do sistema não está disponível. Esta senha será exigida em cada abertura e nunca será armazenada.'
      : 'The system vault is unavailable. This passphrase is required at every opening and is never stored.';
  String get passphraseLabel =>
      _pt ? 'Senha (10 ou mais caracteres)' : 'Passphrase (10+ characters)';
  String get unlockWithPbkdf2 =>
      _pt ? 'Desbloquear com PBKDF2' : 'Unlock with PBKDF2';
}
