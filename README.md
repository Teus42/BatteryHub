# Battery Hub

Centraliza o nível de bateria de celulares/tablets Android no seu PC Windows, pela rede local.
Sem nuvem, sem Firebase, sem custos.

```
Android (a cada N min, padrão 5)  --POST /battery + token-->  PC (bandeja do Windows) --> SQLite + painel + alertas
```

## PC (`pc/`)

Python 3.10+ (testado no 3.14). Dependências: `pystray`, `pillow`.

```powershell
cd pc
pip install -r requirements.txt
pythonw run.py          # ou: python run.py para ver erros no console
```

## Instaladores (`build\`)

```powershell
.\build_all.ps1         # gera tudo em .\build
```

| Arquivo | O que é |
|---|---|
| `BatteryHub-Setup-1.0.0.exe` | Instalador do Windows (mesmo tema do app): escolha da pasta, atalhos, início com o Windows, regra de Firewall |
| `BatteryHub-1.0.0.apk` | App Android de release, assinado |
| `SHA256SUMS.txt` | Somas de verificação |

Requisitos para gerar: Python com `pip install -r pc\requirements.txt`, JDK 17 e Android SDK (`JAVA_HOME`/`ANDROID_HOME`).

- **Instalador:** por padrão instala em `%LOCALAPPDATA%\Programs\Battery Hub`, sem precisar de administrador. Se a pasta escolhida já tiver
  outros arquivos, cria uma subpasta `Battery Hub`. Registra o programa em *Aplicativos instalados* e copia um `Uninstall.exe`.
  Modo sem tela: `BatteryHub-Setup-1.0.0.exe --silent [--dir PASTA] [--firewall]` e `Uninstall.exe --silent [--remove-data]`.
- **Assinatura do APK:** usa `android\battery-hub.keystore` + `android\keystore.properties` (fora do controle de versão).
  **Guarde uma cópia desses dois arquivos**: sem eles não dá para publicar atualizações que instalem por cima.
- Os executáveis não têm assinatura digital, então o SmartScreen pode avisar "editor desconhecido" na primeira execução.

- Ícone na bandeja (área de notificação): clique abre o painel; menu com *Iniciar com o Windows* e *Sair*.
- Fechar a janela só a esconde; o servidor continua rodando.
- Painel: cadastrar, renomear e remover dispositivos. Ao adicionar, aparece um **código de ativação 123-456** (válido por 10 min, uso único); "Parear / Código" gera outro quando precisar (re-parear substitui o token e revoga o app antigo).
- **Clique direito no ícone** abre um popup próprio (tema claro/escuro do Windows) com *Abrir painel*, *Iniciar com o Windows* e *Sair*.
  Acima das ações aparece um cartão com anel de progresso para cada dispositivo marcado na coluna **Na bandeja** do painel
  (clique na célula para marcar/desmarcar). Carregando, o anel mostra uma bateria com raio no lugar da porcentagem.
- Alertas do Windows: bateria baixa (padrão 20%), dispositivo sem reportar (padrão 45 min) e **bateria em 100%** (qualquer dispositivo).
  Os valores do popup e o aviso de 100% podem ter até o intervalo de envio do app Android (padrão 5 min) de atraso.
- Dados em `%APPDATA%\BatteryHub\hub.db` (histórico de 30 dias).

### Localizar dispositivo

Faz o aparelho tocar o alarme (volume máximo) e vibrar, para achá-lo em casa.

- **Painel:** selecione o aparelho e clique em **Localizar**; o botão vira **Parar busca** (âmbar) e o Status mostra
  *Procurando…* (pedido enviado) e depois *Tocando no aparelho* (o aparelho confirmou).
- **Bandeja:** clique no cartão do aparelho no popup. Ele ganha borda azul, ondas animadas e o texto *clique para parar*;
  clicar de novo para a busca. Enquanto houver busca, o ícone da bandeja ganha uma bolinha vermelha e a dica lista os aparelhos.
- O aparelho para sozinho em 10 min. O pedido também vale por 10 min: se o aparelho estiver fora do ar, toca quando reconectar.
- No app Android há um aviso com **Parar toque** e a notificação tem o botão **Parar**.
- **Silencioso e Não Perturbe:** o som usa o canal de alarme do Android, que não é calado pelo modo silencioso, e o app
  sobe o volume do alarme ao máximo (e o restaura depois). O Não Perturbe pode calar o alarme; para tocar mesmo assim,
  use o botão **Permitir no Não Perturbe** no app (o acesso é concedido numa tela do sistema). Com ele, o app desliga o
  bloqueio só enquanto toca e volta ao modo anterior ao parar.

**Firewall:** em *Configurações* use "Liberar porta no Firewall" (pede permissão de administrador; só rede privada).
Reserve também um IP fixo para o PC no roteador, senão o IP muda e os apps deixam de achar o servidor.

### Pareamento

1. No PC: **Adicionar** → nome → aparece o código `123-456` junto com o IP e a porta.
2. No app Android: informe IP e porta, digite o código e toque em **Ativar dispositivo**.
3. O app recebe um token (guardado só no aparelho) e passa a usá-lo como `Bearer` nos envios. O código não serve mais.

O endpoint `/pair` limita tentativas erradas (5/min por IP, 30/10 min no total) para impedir adivinhação do código.

### API

```
POST http://<ip>:8765/pair                      (sem autenticação; só com código válido)
{"code": "123456"}   → 200 {"ok": true, "token": "...", "name": "Tablet"}
                        404 código inválido/expirado   429 muitas tentativas
```

```
POST http://<ip>:8765/battery
Authorization: Bearer <token-do-dispositivo>
{"level": 78, "charging": false, "temperature": 31.2, "voltage": 4.01}   # temperature/voltage opcionais
→ 200 {"ok": true, "server_time": 1760000000}      401 token inválido    400 corpo inválido
GET /health → {"ok": true}
```

```
GET  http://<ip>:8765/events          Authorization: Bearer <token>
     canal aberto pelo aparelho (text/event-stream). O PC escreve "data: find-start" / "data: find-stop";
     envia ": keepalive" a cada 4 min. Um pedido pendente é entregue assim que o aparelho conecta.
POST http://<ip>:8765/find/ack        Authorization: Bearer <token>
     {"ringing": true|false}          o aparelho confirma que começou/parou de tocar
```

## Android (`android/`)

Kotlin, sem dependências externas. Abra a pasta `android/` no **Android Studio**, aguarde o sync e rode no aparelho
(ou *Build > Build APK*). Requer Android 8.0+ (API 26).

- Tela com IP, porta e código de ativação (formato 123-456), intervalo de envio (1 a 1440 min, padrão 5) e botão de teste.
- **Envio automático sempre ativo** depois da ativação, por um serviço em primeiro plano (notificação fixa, silenciosa).
  Reinicia sozinho após o boot e após atualizar o app. Não há interruptor: o app só envia se o dispositivo estiver ativado.
- O ritmo vem de alarmes exatos (`AlarmManager`), não de um timer interno, que atrasaria com a CPU em repouso.
  Cada ciclo lê a bateria (intent sticky), faz um POST e agenda o próximo. Em falha (PC desligado, fora da rede) não há retry.
- **Canal com o PC:** o serviço mantém uma conexão parada (`GET /events`) por onde o PC manda o comando de localizar. Parada,
  ela não trafega nada (só um sinal a cada 4 min) e reconecta sozinha, com espera crescente, se o PC ou o Wi-Fi caírem.
  Com a tela do aparelho apagada por muito tempo e fora do carregador, o modo Doze do Android pode bloquear a rede e atrasar o
  comando; isentar o app da otimização de bateria resolve.
- Intervalos curtos consomem mais bateria. Em Xiaomi/Samsung/Huawei isente o app da otimização de bateria.
- Após reiniciar o aparelho, o envio só volta depois do primeiro desbloqueio (limitação do Android).
- Para Xiaomi/Samsung/Huawei etc.: use os botões *Isentar da otimização de bateria* e *Guia para o meu fabricante*.

## Próximos passos possíveis

Botão "pedir bateria agora" (o canal `/events` já permite), gráfico de histórico, descoberta por mDNS.
