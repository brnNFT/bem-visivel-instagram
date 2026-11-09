# Bem Visível · publicação automática no Instagram

Este repositório publica os posts do [@bem.visivel](https://www.instagram.com/bem.visivel/) sozinho, na data e hora marcadas, pela API oficial do Instagram (Meta). Não precisa deixar computador ligado: quem roda é o GitHub, de graça, a cada 15 minutos.

## Como agendar um post

1. Crie uma pasta dentro de `fila/` com o nome **`AAAA-MM-DD_HHMM_nome`**, no horário de Brasília.
   Exemplo: `fila/2026-11-12_1900_apresentacao` sai em 12/11/2026 às 19h.
2. Coloque dentro dela:
   - as imagens (`.png` ou `.jpg`). Uma imagem vira post simples; de 2 a 10 viram carrossel, **na ordem alfabética dos nomes** (`tela1.png`, `tela2.png`...);
   - um arquivo `legenda.txt` com a legenda e as hashtags.
3. Pronto. Quando chegar a hora, o post é publicado e a pasta vai para `publicados/`, com um `resultado.json` que traz o link do post.

Dá para fazer tudo pelo site do GitHub, sem instalar nada: abra a pasta `fila`, clique em **Add file → Upload files** e arraste as imagens e a legenda. Para criar a pasta, no campo do nome do arquivo digite `fila/2026-11-30_1900_meu-post/legenda.txt`.

Para mudar a data, renomeie a pasta. Para cancelar, apague a pasta.

### Regras que o código confere antes de publicar

- de 1 a 10 imagens, formato entre 4:5 (retrato, como as nossas 1080x1350) e 1,91:1 (paisagem);
- legenda de até 2.200 caracteres, até 30 hashtags e até 20 menções (@);
- no carrossel, todas as imagens são cortadas no formato da primeira, então use o mesmo tamanho em todas;
- **no máximo um post por rodada** (a cada 15 minutos), para nunca sair tudo de uma vez;
- **post com mais de 24 horas de atraso não sai sozinho**: ele fica parado com um `erro.txt`, e você escolhe uma nova data.

Quando algo dá errado, a pasta ganha um `erro.txt` explicando o motivo e o post fica parado. Corrija e apague o `erro.txt` para ele tentar de novo.

## Configuração (uma vez só)

São uns 20 minutos. Use o computador, porque o painel da Meta é ruim no celular.

### 1. Criar o app na Meta

A conta @bem.visivel já é profissional, que é o que a API exige.

1. Entre em [developers.facebook.com](https://developers.facebook.com/) com uma conta do Facebook e aceite virar desenvolvedor (é gratuito e não aparece em lugar nenhum público).
2. Clique em **Meus apps → Criar app**. Nome: `Bem Visível Publicador`. E-mail: `contato.bemvisivel@gmail.com`.
3. No caso de uso, escolha **"Gerenciar mensagens e conteúdo no Instagram"**. Conclua. O app fica em **modo de desenvolvimento**, e é assim que ele deve ficar.

### 2. Gerar o token

Há dois caminhos. O robô aceita os dois e descobre sozinho qual foi usado.

**Caminho A: login do Instagram (mais simples, se a Meta deixar).** Em **Casos de uso → Personalizar → API setup with Unified login**, a Meta pede um *portfólio empresarial*. Se você conseguir criar ou selecionar um, siga: **Gerar tokens de acesso → Adicionar conta → @bem.visivel → Gerar token**. O token começa com `IG` e vale 60 dias; o robô renova sozinho.

**Caminho B: Página do Facebook (quando a Meta bloqueia o portfólio).**
1. Crie uma Página do Facebook em [facebook.com/pages/create](https://www.facebook.com/pages/create): nome **Bem Visível**, categoria **Organização sem fins lucrativos**.
2. Ligue a Página ao Instagram: no app do Instagram, em @bem.visivel, **Editar perfil → Página → Conectar uma Página do Facebook existente**.
3. No painel da Meta, abra **Ferramentas → Explorador da Graph API** (*Graph API Explorer*).
   - Em **App da Meta**, escolha `Bem Visível Publicador`.
   - Em **Permissões**, adicione: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `business_management`.
   - Clique em **Generate Access Token**, entre no Facebook, escolha a Página Bem Visível e a conta @bem.visivel, e aceite tudo.
4. Esse token dura só 1 hora. Para estender: abra **Ferramentas → Depurador de token de acesso**, cole o token, clique em **Depurar** e depois em **Estender token de acesso**. Copie o token estendido (vale 60 dias).
5. Volte ao Explorador, cole o token estendido no campo do token e, no menu **Usuário ou Página**, escolha a Página **Bem Visível**. O campo passa a mostrar o **token da Página**, que começa com `EAA` e **não vence**. Copie este.

Em qualquer caminho: **o token dá poder de publicar na conta. Não mande por e-mail, WhatsApp ou chat, nem coloque em arquivo do repositório.** Ele vai só no cofre do GitHub.

Não precisa enviar o app para revisão da Meta: como ele só publica na nossa própria conta, o modo de desenvolvimento basta.

### 3. Guardar o token no GitHub

1. Neste repositório, abra **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `IG_TOKEN`. Secret: cole o token. Salve.
3. (Opcional) Crie outro segredo `IG_USER_ID` com o ID da conta.

### 4. Testar sem publicar nada

1. Abra a aba **Actions → Publicar no Instagram → Run workflow**.
2. Deixe marcada a opção **"Só simular"** e clique em **Run workflow**.
3. Em um minuto, abra a execução. O passo "Testar o token" deve mostrar `Conta: bem.visivel` e o limite de posts. O passo "Publicar" mostra o que seria publicado.

Se ficou verde, está tudo certo: a partir daí ele roda sozinho a cada 15 minutos.

### Se der erro

- **"Falta a variável IG_TOKEN"**: o segredo não foi criado com esse nome exato.
- **"a API respondeu 190"** ou "token inválido": o token venceu ou foi copiado pela metade. Gere outro no passo 2 e troque o segredo.
- **"Permission denied" ao fazer push**: em **Settings → Actions → General → Workflow permissions**, marque **Read and write permissions**.
- **Avisos por e-mail**: o GitHub manda e-mail quando uma execução falha. Confira em [github.com/settings/notifications](https://github.com/settings/notifications), na parte **Actions**, que a opção de falhas está ligada.

## Testar no computador (opcional)

```bash
pip install -r requirements.txt
python publicar.py verificar                       # mostra a fila e aponta problemas
python publicar.py verificar --agora 2026-11-12T19:05
python publicar.py publicar --simular --agora 2026-11-12T19:05
python -m unittest -v                              # testes automáticos
```

Nenhum desses comandos publica nada nem precisa de token.

## Limites que vale saber

- **Até 100 posts por dia** pela API (carrossel conta como um). Não chegamos nem perto.
- **Só JPEG**: a API não aceita PNG. Por isso o robô converte as imagens e guarda as cópias em `_jpg/` dentro de cada pasta.
- **As imagens precisam estar num endereço público** na hora da publicação, porque é o Instagram que vai buscá-las. Por isso o repositório é público. Quem olhar o repositório vê os posts agendados antes da hora, o que não é problema para conteúdo que vai ser público de qualquer jeito. **Nunca coloque aqui comprovantes com dados pessoais sem tarja.**
- **Atraso de alguns minutos**: o GitHub às vezes começa a rodada agendada 5 a 20 minutos depois.
- **Não publica**: stories (a API até aceita, mas este robô ainda não faz), filtros, música, marcação de produtos, colaboração e marcação de pessoas na foto. Para isso, use o app do Instagram.
- **Token**: no caminho A ele vale 60 dias e o robô renova toda segunda-feira; se a Meta devolver um token diferente, a execução "Renovar token" falha e avisa por e-mail. No caminho B o token da Página não vence; a mesma execução só confere se continua válido.
- **Pausa por inatividade**: o GitHub desliga tarefas agendadas de repositórios sem nenhuma alteração por 60 dias. Cada post publicado conta como alteração, então isso só acontece se a página ficar dois meses sem post. Se acontecer, a aba Actions mostra um botão para religar.
- **Plano B**: o Meta Business Suite (business.facebook.com) também agenda posts e carrosséis, de graça, à mão.
