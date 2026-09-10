# Iris - Aplicativo Android

Cliente Android nativo moderno para o sistema de busca multimodal e gerenciador de memes **Iris**.

Desenvolvido com **Kotlin**, **Jetpack Compose** e **Material 3**, seguindo o design system e paleta icônica do Iris (Dark Neon Lime & Surface Charcoal).

---

## 📱 Funcionalidades

1. **Galeria Multimodal**:
   - Grade adaptativa de mídias com rolagem infinita.
   - Suporte a imagens e vídeos (com identificador visual e badges).
   - Filtros por tipo de mídia (*Todas*, *Imagens*, *Vídeos*).
   - Suporte a Pull-to-refresh.
   - Indicador em tempo real de status da conexão com o servidor Iris.

2. **Busca Semântica & Multimodal**:
   - Alternância entre busca **Semântica (IA / CLIP)** e busca por **Nome de Arquivo**.
   - Slider de balanço dinâmico: **Visual** (Cores/Objetos) vs. **Conceitual** (Significado/Texto).
   - Exibição de pontuação de similaridade (*score %*).
   - Ação de busca aleatória (*Surpreenda-me*).

3. **Visualizador de Detalhes (Estilo Google Photos)**:
   - Visualização de imagens em alta resolução com pinch-to-zoom (gestos de zoom e arrasto).
   - Player nativo de vídeo integrado com **Media3 ExoPlayer**.
   - Exibição do texto extraído por **OCR** com botão de copiar para a área de transferência.
   - Descrição visual gerada por IA (**Florence-2**).
   - Chips de identificação de rostos e pessoas reconhecidas.
   - Lista horizontal de mídias similares baseadas nos embeddings CLIP.
   - Ação de compartilhamento de link/metadados.

4. **Reconhecimento Facial (Pessoas)**:
   - Listagem de pessoas detectadas com miniaturas dos rostos (`/api/faces/{id}/thumb`).
   - Grade dedicada com todas as mídias de uma pessoa selecionada.

5. **Coleções & Conceitos**:
   - Navegação por coleções personalizadas.
   - Navegação por conceitos abstratos e semânticos.

6. **Configuração do Servidor**:
   - Configuração de URL do servidor (ex: `http://10.0.2.2:8000/` para emulador, IP da rede local ou domínio remoto).
   - Botão para testar conexão com diagnóstico imediato.
   - Painel informativo com status do modelo CLIP, dispositivo de aceleração (CUDA/CPU), banco SQLite e índice FAISS.

---

## 🛠️ Stack Tecnológica

- **Linguagem:** Kotlin 2.0.21
- **UI Toolkit:** Jetpack Compose + Material 3
- **Carregamento de Imagens:** Coil 2.7.0 (com decodificador de quadros de vídeo)
- **Reprodução de Vídeo:** AndroidX Media3 ExoPlayer 1.5.1
- **Rede & API:** Retrofit 2.11.0 + OkHttp 4.12.0 + Kotlinx Serialization
- **Persistência de Configurações:** Jetpack DataStore Preferences
- **Navegação:** AndroidX Navigation Compose 2.8.5
- **Mínimo SDK:** Android 8.0 (API 26)
- **Target / Compile SDK:** Android 15 (API 35)

---

## 🚀 Como Compilar e Executar

### Pré-requisitos
- JDK 21
- Android SDK (API 35)

### Build do APK Debug
```bash
./gradlew assembleDebug
```
O arquivo APK gerado estará em:
`app/build/outputs/apk/debug/app-debug.apk`

### Executar Testes Unitários
```bash
./scripts/test.sh fast
```

## Loop de teste rápido

Use estes comandos durante o desenvolvimento:

```bash
# Testes JVM: URL, autenticação e contratos de rede. Não exige Android.
./scripts/test.sh fast

# Testes rápidos mais compilação do APK debug.
./scripts/test.sh build

# Testes instrumentados de mídia no emulador Android já iniciado.
./scripts/test.sh device
```

O modo `device` seleciona explicitamente um AVD (`emulator-*`) e nunca instala nada
em um celular físico, mesmo que ele esteja conectado por USB. Os testes criam
credenciais temporárias, por isso use um AVD descartável.
Os testes de mídia verificam que previews e vídeos privados recebem o token Bearer;
assim uma falha 401 é detectada antes de instalar o APK manualmente.

Para preparar o AVD descartável pela primeira vez, com as variáveis do Android SDK
configuradas, rode:

```bash
./scripts/create-emulator.sh
${ANDROID_SDK_ROOT}/emulator/emulator -avd Iris-Test-API35
./scripts/test.sh device
```

O pipeline do GitHub executa `fast` e compila tanto o APK quanto os testes
instrumentados em toda alteração do diretório `android/`. A execução de interface
fica localmente no emulador para manter o pipeline rápido e previsível.
