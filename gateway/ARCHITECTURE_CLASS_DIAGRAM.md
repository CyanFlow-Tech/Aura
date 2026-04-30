# Aura Gateway 模块依赖全景图

## 图1：类定义（不含关系）

| 类名 | 类型/标记 | 字段与方法 |
| --- | --- | --- |
| `aura_server` | - | `+upload(audio_file, session_id)`<br>`+text_stream(session_id)`<br>`+audio_stream(session_id)`<br>`+interrupt(session_id)`<br>`+session_complete(session_id)` |
| `Aura` | - | `+tts: TextToSpeech`<br>`+stt: SpeechToText`<br>`+searching: Searching`<br>`+llm: LLM` |
| `SessionManager` | - | `-_sessions: dict~str, Session~`<br>`+get_session(session_id) Session`<br>`+start_turn(session_id, build) Session`<br>`+interrupt_session(session_id) bool`<br>`+complete_session(session_id) bool`<br>`+subscribe(session, channel_name) ReceiveChannel`<br>`+stream(session, channel_name)` |
| `SessionError` | `<<exception>>` | - |
| `SessionNotFoundError` | `<<exception>>` | - |
| `NoActiveTurnError` | `<<exception>>` | - |
| `Session` | - | `+session_id: str`<br>`+conversation: Conversation`<br>`-_current_turn: Turn`<br>`+start_turn(bundle) Turn`<br>`+interrupt_current_turn() bool`<br>`+release() None` |
| `Turn` | - | `+stages: list~Stage~`<br>`+channels: list~Any~`<br>`+endpoints: dict~str, Any~`<br>`+start(name) None`<br>`+cancel() None`<br>`+subscribe(name) ReceiveChannel` |
| `Conversation` | - | `+session_id: str`<br>`+history: list~dict~`<br>`+append_user(text) None`<br>`+append_assistant(text) None`<br>`+messages(system_prompt, extra_system_messages) list~dict~`<br>`+recent_history(limit) list~dict~` |
| `PipelineBundle` | - | `+stages: list~Stage~`<br>`+channels: list~Any~`<br>`+endpoints: dict~str, Any~` |
| `Stage` | `<<protocol>>` | `+run() None` |
| `STTStage` | - | `-_stt: SpeechToText`<br>`-_buffer: BytesIO`<br>`-_out: SendChannel~str~`<br>`+run() None` |
| `BaseConversationStage` | - | `-_llm: LLM`<br>`-_conversation: Conversation`<br>`-_out: SendChannel~str~`<br>`+run() None`<br>`#_prepare_turn() tuple~str, list~`<br>`#_stream_reply(messages) str` |
| `ConversationStage` | - | `-_inp: ReceiveChannel~str~`<br>`#_prepare_turn() tuple~str, list~` |
| `SearchIntentStage` | - | `-_llm: LLM`<br>`-_conversation: Conversation`<br>`-_inp: ReceiveChannel~str~`<br>`-_out: SendChannel~UserTurnContext~`<br>`+run() None`<br>`-_plan(user_text) tuple~bool, str~` |
| `SearchStage` | - | `-_searching: Searching`<br>`-_inp: ReceiveChannel~UserTurnContext~`<br>`-_out: SendChannel~UserTurnContext~`<br>`+run() None` |
| `SearchAugmentedConversationStage` | - | `-_inp: ReceiveChannel~UserTurnContext~`<br>`#_prepare_turn() tuple~str, list~`<br>`-_format_retrieval_context(turn) str` |
| `TTSStage` | - | `-_tts: TextToSpeech`<br>`-_inp: ReceiveChannel~str~`<br>`-_out: SendChannel~tuple~`<br>`+run() None` |
| `UserTurnContext` | - | `+user_text: str`<br>`+should_search: bool`<br>`+search_query: str`<br>`+search_results: list~SearchResult~` |
| `SendChannel` | `<<protocol>>` | `+send(item) None`<br>`+close() None` |
| `ReceiveChannel` | `<<protocol>>` | `+receive()`<br>`+__aiter__()` |
| `QueueChannel~T~` | - | `-_q: asyncio.Queue`<br>`-_closed: bool`<br>`+send(item) None`<br>`+close() None`<br>`+receive() T`<br>`+replay(items, closed) None` |
| `BroadcastChannel~T~` | - | `-_history: list~T~`<br>`-_subs: list~QueueChannel~`<br>`-_closed: bool`<br>`+subscribe() QueueChannel~T~`<br>`+send(item) None`<br>`+close() None` |
| `LLMProviderSpec` | - | `+name: str`<br>`+parser: Callable`<br>`+api_url_env: str`<br>`+api_key_env: str` |
| `LLM` | - | `+provider: str`<br>`+model_name: str`<br>`+api_url: str`<br>`+system_prompt: str`<br>`+char_separators: set`<br>`+char_batch_size: int`<br>`+generate(messages, think)`<br>`+parse_response(response)`<br>`+generate_text(messages, think) str` |
| `TextToSpeech` | `<<abstract>>` | `+text_to_speech(text) bytes`<br>`+_text_to_speech(text) bytes`<br>`+build(config) TextToSpeech` |
| `CosyVoice` | - | `+api_url: str`<br>`+voice: str`<br>`+_text_to_speech(text) bytes` |
| `EdgeTTS` | - | `+voice: str`<br>`+_text_to_speech(text) bytes` |
| `SpeechToText` | `<<abstract>>` | `+speech_to_text(audio_buffer) str`<br>`+build(config) SpeechToText` |
| `Whisper` | - | `+model`<br>`+language: str`<br>`+prompt: str`<br>`+speech_to_text(audio_buffer) str` |
| `Searching` | `<<abstract>>` | `+search(query, limit) list~SearchResult~`<br>`+build(config) Searching` |
| `SearXNG` | - | `+api_url: str`<br>`+search(query, limit) list~SearchResult~` |
| `SearchResult` | - | `+title: str`<br>`+url: str`<br>`+content: str` |
| `FactoryMixin` | `<<mixin>>` | `+register_impl()`<br>`+get_impl(name)`<br>`+build(config)` |
| `AutoConfigMixin` | `<<mixin>>` | `+_params: list` |
| `HeartbeatAssets` | - | `+content_mp3: bytes`<br>`+content_duration_s: float`<br>`+silence_mp3: bytes`<br>`+silence_duration_s: float`<br>`+enabled: bool` |

## 图2：类间关系（非类图）

```mermaid
flowchart LR
    aura_server -->|manages| SessionManager
    aura_server -->|uses| Aura
    aura_server -->|build callback arg| Conversation
    aura_server -->|creates via factory| PipelineBundle
    aura_server -->|uses| HeartbeatAssets
    aura_server -->|maps to HTTP 404| SessionNotFoundError
    aura_server -->|maps to HTTP 409| NoActiveTurnError

    SessionNotFoundError -->|inherits| SessionError
    NoActiveTurnError -->|inherits| SessionError

    SessionManager -->|owns| Session
    Session -->|owns| Conversation
    Session -->|owns current| Turn
    Turn -->|runs| Stage
    Turn -->|subscribes| ReceiveChannel
    Turn -->|endpoint may be| BroadcastChannel

    PipelineBundle -->|contains| Stage
    PipelineBundle -->|contains| QueueChannel
    PipelineBundle -->|contains| BroadcastChannel

    STTStage -.->|implements| Stage
    BaseConversationStage -.->|implements| Stage
    SearchIntentStage -.->|implements| Stage
    SearchStage -.->|implements| Stage
    TTSStage -.->|implements| Stage

    ConversationStage -->|inherits| BaseConversationStage
    SearchAugmentedConversationStage -->|inherits| BaseConversationStage

    STTStage -->|uses| SpeechToText
    STTStage -->|writes| SendChannel

    BaseConversationStage -->|uses| LLM
    BaseConversationStage -->|updates| Conversation
    BaseConversationStage -->|writes| SendChannel

    ConversationStage -->|reads| ReceiveChannel
    SearchIntentStage -->|reads| ReceiveChannel
    SearchIntentStage -->|writes| SendChannel
    SearchIntentStage -->|uses| LLM
    SearchIntentStage -->|reads history| Conversation

    SearchStage -->|uses| Searching
    SearchStage -->|reads| ReceiveChannel
    SearchStage -->|writes| SendChannel
    SearchStage -->|mutates| UserTurnContext

    SearchAugmentedConversationStage -->|reads| ReceiveChannel
    SearchAugmentedConversationStage -->|consumes| UserTurnContext

    TTSStage -->|uses| TextToSpeech
    TTSStage -->|reads| ReceiveChannel
    TTSStage -->|writes| SendChannel

    QueueChannel -.->|implements| SendChannel
    QueueChannel -.->|implements| ReceiveChannel
    BroadcastChannel -.->|implements| SendChannel
    BroadcastChannel -->|creates subscribers| QueueChannel

    Aura -->|holds| TextToSpeech
    Aura -->|holds| SpeechToText
    Aura -->|holds| Searching
    Aura -->|holds| LLM

    LLM -->|resolves strategy| LLMProviderSpec

    CosyVoice -->|inherits| TextToSpeech
    EdgeTTS -->|inherits| TextToSpeech
    Whisper -->|inherits| SpeechToText
    SearXNG -->|inherits| Searching

    TextToSpeech -->|inherits| AutoConfigMixin
    TextToSpeech -->|inherits| FactoryMixin
    SpeechToText -->|inherits| AutoConfigMixin
    SpeechToText -->|inherits| FactoryMixin
    Searching -->|inherits| AutoConfigMixin
    Searching -->|inherits| FactoryMixin

    SearchStage -->|outputs| SearchResult
    SearchIntentStage -->|creates| UserTurnContext
```
