# Visao de produto do Iris

> Estado: visao de produto confirmada em 2026-09-23. Este documento registra
> as regras de produto; o plano de entrega esta em
> [implementation-roadmap.md](implementation-roadmap.md).

## Proposito

Iris e uma galeria privada, auto-hospedada e orientada a fotos e videos. Uma
pessoa, familia, empresa ou tecnico instala uma instancia central em um
computador ou servidor; as pessoas autorizadas conectam seus aplicativos e
navegadores a ela. Cada instalacao administra suas proprias contas e dados.
A experiencia deve ser familiar para quem usa uma galeria: navegar por data,
pessoas, albuns e busca, sem precisar entender bancos, indices, modelos de IA
ou a antiga linguagem de "memes".

O referencial de experiencia e Google Fotos / Apple Fotos; o referencial de
produto auto-hospedado e Immich. Iris nao pretende copiar cada recurso desses
produtos nem depender de uma VPN especifica. Tailscale e uma boa opcao de
implantacao para o instalador, mas nao faz parte da identidade do aplicativo.

## Principios confirmados

1. **Pessoas comuns primeiro.** A interface normal usa linguagem de galeria:
   itens, albuns, pessoas, data e dispositivo. Diagnosticos, pontuacoes e
   controles de IA pertencem a uma area avancada.
2. **Privacidade por conta.** Cada conta possui uma biblioteca privada. Uma
   conta administrativa administra acesso e instalacao, mas nao ganha, por
   isso, uma tela para navegar a biblioteca de outra pessoa.
3. **Instancia central para varias pessoas e dispositivos.** Uma instalacao
   pode servir uma pessoa, uma familia ou uma equipe. Uma conta pode
   representar uma pessoa ou uma empresa; cada conta tem uma biblioteca unica,
   configuracoes e sincronizacoes isoladas. A empresa pode usar uma conta
   propria, criar contas para funcionarios e compartilhar albuns entre elas
   conforme preferir. Essa biblioteca agrega pastas e dispositivos de origem;
   eles nao criam bibliotecas independentes dentro da conta. No primeiro uso
   familiar, mae, pai, irmaos e demais membros terao contas individuais no
   mesmo servidor, cada uma com suas fotos, videos, pastas e preferencias.
4. **Compartilhamento entre contas no MVP.** Uma instalacao pode ter varios
   espacos compartilhados independentes, cada um acessivel a um subconjunto
   proprio de contas autorizadas. A familia e o primeiro caso de uso, nao um
   limite do produto: equipes e outros grupos tambem devem poder usa-los.
   Um espaco nao torna bibliotecas privadas automaticamente visiveis aos
   outros. Cada pessoa entra com sua propria conta; nao ha credenciais
   coletivas. Ao adicionar uma foto ao espaco, ela ganha uma copia logicamente
   independente: apagar o item da biblioteca privada nao remove o item
   compartilhado. Isso nao exige duplicar bytes no disco se houver
   deduplicacao segura. Cada espaco tem uma galeria propria, com itens que
   podem ser organizados em albuns; nao e apenas um unico album colaborativo.
   A participacao no espaco distingue visualizadores (somente leitura) de
   colaboradores (podem tambem adicionar itens). Esses niveis pertencem ao
   espaco, nao substituem administrador e membro da instalacao. Um colaborador
   pode remover do espaco apenas itens que adicionou; um gestor do espaco pode
   remover qualquer item; visualizadores nao removem. Essa remocao nao apaga
   a copia privada de outro membro. Qualquer conta pode criar um espaco; quem
   o cria se torna seu gestor, convida contas, concede o nivel de acesso de
   cada uma e pode nomear outros gestores. O administrador da instalacao nao
   recebe acesso automatico a fotos do espaco. Se o ultimo gestor tiver a
   conta excluida, o administrador pode nomear outro membro como gestor sem
   ganhar acesso as fotos. As contribuicoes permanecem no espaco se a conta
   que as adicionou sair ou perder acesso; nesse caso, seu acesso cessa
   imediatamente. O ultimo gestor nao pode sair voluntariamente antes de
   nomear um substituto. O acesso inicial e restrito a contas autenticadas
   no mesmo servidor; links publicos ficam para uma decisao e implementacao
   futuras. Um visualizador pode baixar itens ou salvar copias na propria
   biblioteca: somente leitura impede alterar o espaco, nao obter copias.
   Somente um gestor pode excluir o espaco inteiro, com confirmacao reforcada
   e recuperacao durante 30 dias antes da exclusao definitiva.
5. **Servidor como fonte de verdade.** O servidor conserva os originais e o
   catalogo; dispositivos mantem cache reconstruivel e uma fila duravel de
   envio. Aceitar um upload e processa-lo com IA sao fases separadas.
6. **Origem importa.** Uma foto enviada do celular preserva o dispositivo e a
   pasta de origem como metadados. Pasta de origem vira uma visao derivada para
   revisar ou limpar lotes, nunca um album criado implicitamente.
7. **Desempenho mensuravel.** Otimizacoes devem partir de metricas de ponta a
   ponta (toque, estado, rede, dados e desenho), incluindo aparelhos fracos;
   nao de limites artificiais de layout.
8. **Instalar e atualizar deve ser simples.** Uma implantacao auto-hospedada
   precisa ter configuracao explicita, preservacao de dados e comandos
   previsiveis de install/update.
9. **Papeis iniciais minimos da instalacao.** Existem somente administrador e
   membro na instancia. Contas infantis, convidados e papeis de cuidador sao
   extensoes futuras, nao aliases ambiguos do membro comum. Permissoes de
   participacao em cada espaco compartilhado sao separadas desses papeis.
10. **Cadastro controlado por instancia.** Somente um administrador cria contas
    na instalacao inicial. Pedidos de acesso e autocadastro ficam para uma
    decisao futura. Ao excluir uma conta, seu acesso e desativado
    imediatamente, mas a biblioteca privada permanece recuperavel por 30
    dias antes da exclusao definitiva. Itens ja adicionados a espacos
    compartilhados permanecem nesses espacos.
11. **Acervos podem crescer muito.** O Iris nao deve assumir que uma conta
    equivale a um acervo domestico pequeno: uma equipe pode acumular centenas
    de milhares ou milhoes de itens. Esse e um objetivo de escala do produto,
    nao uma capacidade ja comprovada pela implementacao atual.
12. **Clientes iniciais.** Navegador e aplicativo Android sao os clientes do
    primeiro produto completo. iPhone/iPad e clientes desktop nativos podem
    ser planejados depois; a API deve permitir novos clientes.

## Experiencia alvo

### Biblioteca

- Grade cronologica com separacao por data, zoom por gesto e rolagem rapida por
  mes/ano.
- Fotos e videos tratados como itens de primeira classe; miniaturas e estados de
  carregamento deixam claro quando algo ainda esta sendo buscado.
- GIFs e SVGs aparecem na galeria principal como imagens. Arquivos de audio
  continuam pesquisaveis, mas ficam em uma area propria, separada da grade
  cronologica de fotos e videos. A sincronizacao inicial do Android cobre
  fotos e videos.
- Abrir um item mostra a midia primeiro. Um segundo toque revela acoes simples
  (compartilhar, baixar, detalhes, renomear); dados tecnicos ficam em
  **Avancados**.
- Pessoas e albuns sao entradas de navegacao primarias. Tags e inferencias de
  IA ajudam na busca, mas nao poluem a estrutura principal.
- Espacos compartilhados tem navegacao propria. Itens desses espacos nao
  aparecem automaticamente na grade da biblioteca privada.
- A busca inclui por padrao a biblioteca privada e todos os espacos aos quais
  a conta tem acesso. Cada resultado identifica sua origem, e um filtro
  permite restringir a busca a uma biblioteca ou espaco especifico.

### Android e sincronizacao

- A instalacao/migracao de um acervo ja existente no servidor e uma operacao
  administrativa explicita. O primeiro login no Android guia somente a
  configuracao de sincronizacao do dispositivo e da conta que entrou.
- O Android pode conservar perfis separados para diferentes pares de conta e
  servidor, mas apenas um perfil fica ativo por vez: so ele aparece e
  sincroniza. Credenciais, cache e fila de envio nao se misturam entre perfis.
  Isso e comportamento-alvo; o app atual ainda usa uma unica URL, sessao e
  catalogo local.
- A pessoa escolhe sincronizar fotos, videos ou ambos, e pode incluir todas as
  pastas visiveis ou apenas pastas selecionadas.
- O envio automatico do dispositivo alimenta somente a biblioteca privada da
  conta ativa. Colocar um item em um espaco compartilhado exige uma acao
  explicita de quem tem permissao; nunca e efeito implicito do backup.
- A grade unificada indica, sem duplicar itens, se algo esta somente no telefone,
  na fila, enviando, processando, disponivel no Iris ou somente no Iris.
- Metadados e miniaturas entram em cache local automaticamente para a galeria
  iniciar sem rede. Originais permanecem no servidor e so viram copia local por
  download explicito; albuns offline nao fazem parte da primeira versao.
- Desmarcar uma pasta para de enviar novos itens; nao apaga originais.
- Exclusoes devem ser operacoes explicitas e diferentes: remover do dispositivo,
  remover do Iris ou remover de ambos.
- Toda exclusao no Iris passa por uma lixeira recuperavel por 30 dias, com
  data de exclusao definitiva visivel. Apos esse prazo, o Iris apaga os
  originais automaticamente.
  **Remover do Iris** preserva copias locais. **Remover de ambos** exige
  confirmacao reforcada e propaga a exclusao aos dispositivos autorizados
  quando voltarem a conectar. Esta e a regra de produto desejada; a
  propagacao entre dispositivos ainda precisa ser implementada.
- Em um album que apenas referencia um item da biblioteca privada, se o dono
  enviar esse item para a lixeira, convidados perdem acesso imediatamente.
  Quem quiser preserva-lo precisa salvar uma copia na propria biblioteca antes
  da exclusao. Em um espaco compartilhado, apagar a copia pessoal nao apaga a
  compartilhada. Remover diretamente do espaco segue as permissoes do espaco
  e a regra geral de lixeira recuperavel por 30 dias.

## Fronteiras e termos

Os termos canonicos estao em [CONTEXT.md](../CONTEXT.md). Em especial:

- **Album** expressa uma organizacao intencional do usuario.
- **Fonte do dispositivo** expressa de onde um item foi lido no telefone.
- **Biblioteca privada** pertence a uma conta e nao e uma categoria de album.
- "Meme" e legado tecnico/linguagem antiga, nao uma categoria de produto.

## Consequencias arquiteturais ja aceitas

- O isolamento e fisico por conta: banco, indices e raiz de midia distintos;
  nao um filtro cosmetico `owner_id` sobre um catalogo compartilhado.
- O acesso nao pode assumir Tailscale: URL/base do servidor e autenticacao sao
  configuraveis pelo cliente. Tailscale, ZeroTier, proxy reverso ou exposicao
  publica sao escolhas de quem instala.
- Criptografia ponta a ponta nao pertence ao fluxo normal enquanto o servidor
  gera miniaturas, OCR, busca semantica e reconhecimento de rostos. Se existir,
  sera um modo de cofre privado separado.

## Decisoes ainda a detalhar

As regras principais de contas, bibliotecas, espacos e clientes estao
registradas acima. Antes da implementacao, ainda sera preciso detalhar fluxos
de interface, modelo de dados, recuperacao, migracao e criterios de teste.

## Criterio de coerencia

Uma nova funcionalidade pertence ao produto se ajuda uma pessoa a guardar,
encontrar, entender, compartilhar ou administrar suas proprias fotos e videos
sem enfraquecer privacidade, confiabilidade ou simplicidade. Caso exista apenas
porque um indice ou modelo a torna possivel, ela deve ficar fora da navegacao
principal ate haver um caso de uso humano claro.
