# Contrato de migracao e recuperacao

> Contrato para mudancas de esquema, backup e atualizacao. Os comandos
> operacionais estao em [server-deployment.md](server-deployment.md); o backup
> atual cobre dados e midia, enquanto `.env` e Compose devem ser guardados
> separadamente.

## Unidade de recuperacao

Uma instalacao inclui, no minimo, o cadastro de contas e dispositivos
(`data/users.db`), o segredo que valida sessoes (`data/secret_key`), cada
banco e raiz de midia de conta, configuracoes locais necessarias ao arranque
e os espacos compartilhados: `shared_spaces`/`shared_space_members` em
`users.db` e, para cada espaco, `data/spaces/<id>/space.db` e
`data/spaces/<id>/media/` (as miniaturas sao reconstruiveis). Com
`IRIS_SPACE_STORAGE=reflink` ou `auto` em btrfs/XFS, copias compartilham blocos
no disco de origem; a ferramenta de backup precisa copiar bytes, nao presumir
que o destino tambem preserva reflinks. Com `hardlink`, espaco e biblioteca
pessoal podem ser o mesmo inode: restaurar um sem o outro separa os arquivos,
o que e seguro, mas deixa de economizar espaco.

`space.db` registra seu esquema em `PRAGMA user_version` (atual: 3). A migracao
de 1 para 2 ocorre em lugar, dentro de uma unica transacao, na primeira
abertura; a migracao de 2 para 3 adiciona os dados de albuns e busca. Uma
versao maior que a conhecida e recusada. Restaurar so `users.db` ou so uma biblioteca pode produzir
referencias quebradas e acesso incorreto. Indices e miniaturas reconstruiveis
podem ser excluidos do backup se houver verificacao de reconstrucao.

O modulo atual `core/backup.py` cria snapshots de um catalogo individual e
um manifesto de midia **por referencia**; o arquivo de snapshot nao contem os
bytes das fotos. Em modo privado multiusuario, os endpoints antigos de backup
do host estao indisponiveis. Portanto, esse snapshot isolado nao e backup
completo da instalacao.

`core/instance_backup.py` e o backup multiusuario atual: copia os bancos por
SQLite, os originais e a lixeira; confere as referencias dos catalogos e
recusa publicar uma copia se faltarem bytes ou se o estado mudar durante a
captura. Uma execucao falha nao aplica a retencao. O snapshot nao inclui
`.env` nem arquivos Compose do host. Testes com dados descartaveis cobrem a
restauracao, mas cada nova migracao ainda precisa dos ensaios abaixo.

## Regras para cada migracao

1. Identificar versao antiga e nova, objetivo, arquivos/esquemas afetados e
   se existe conversao de conteudo. Uma versao desconhecida deve ser recusada,
   nao adivinhada.
2. Fazer copia consistente dos bancos SQLite (API de backup ou servico parado)
   e dos originais envolvidos. Capturar o estado de contas, espacos e midia
   como uma mesma unidade logica. Registrar inventario e hashes para verificar
   a restauracao.
3. Executar a migracao em uma copia descartavel antes de tocar numa
   instalacao real. A operacao deve ser repetivel ou detectar que ja terminou
   sem duplicar contas, itens ou permissoes.
4. Nao remover nem mover originais enquanto a migracao de referencias nao
   tiver sido verificada. Se falhar, impedir o arranque da versao nova sobre
   estado parcialmente convertido e fornecer caminho de restauracao.
5. Testar rollback com a versao anterior e restauracao completa; nunca
   prometer rollback apenas porque o banco abre na versao nova.

## Criterios de teste antes de publicar

- Instalacao limpa e atualizacao de uma instalacao descartavel com duas
  contas, dois dispositivos e fotos sinteticas em cada biblioteca.
- Depois da atualizacao, contagens, hashes e acesso autorizado batem com o
  estado anterior; uma conta nao le bytes da outra.
- Interrupcao simulada em cada etapa mutavel nao deixa o servidor servir
  uma mistura de esquemas nem apagar originais.
- Restauracao volta a abrir as contas corretas e suas midias; tokens podem
  ser invalidados deliberadamente, mas isso deve estar documentado.
- Quando espacos compartilhados existirem, incluir seus membros, papeis,
  albuns, copias independentes e lixeira nos ensaios de atualizacao.

Os testes de instalacao atuais (`scripts/test_release.sh`) cobrem instalacao
limpa, nao esse ciclo de atualizacao e restauracao. Ampliar esse ensaio e
parte da etapa 4 do [plano](implementation-roadmap.md).

O ensaio `tests/test_disposable_recovery.py` ja prova uma restauracao manual
completa de duas contas em diretorio temporario: copia SQLite consistente de
cada banco, inclusive o cadastro de espacos e membros em `users.db`, segredo e
bytes de midia; depois valida login e isolamento. Ele nao
substitui uma rotina operacional de backup, nao testa mudanca de esquema e nao
autoriza executar essa sequencia sobre dados reais.
