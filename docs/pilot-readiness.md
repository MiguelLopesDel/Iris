# Critérios de aceite do piloto familiar

O primeiro lançamento do Iris é um piloto para a família, acessível somente
pela rede privada do servidor. Estes critérios não afirmam suporte público,
suporte a iOS ou escala de milhões de itens.

| Área | Critério para liberar o piloto |
| --- | --- |
| Versão | O código liberado está identificado por commit; CI e smoke test passam nesse mesmo código, sem arquivos locais fora do commit. |
| Contas e privacidade | Duas contas conseguem entrar; cada uma vê apenas a própria biblioteca. Espaços respeitam visualizador, colaborador e gestor em listagem, álbum, busca, miniatura, original, download e alteração. |
| Galeria web e Android | Navegar por fotos e vídeos, abrir previews e originais e voltar entre telas sem travar, perder posição de forma inesperada ou mostrar erro de resposta. |
| Sincronização Android | Upload de foto e vídeo sobrevive a offline, retomada do app e reinício do servidor; repetição não cria cópias; estado e falha ficam visíveis. |
| Compartilhamento familiar | Adicionar ao espaço é explícito; a cópia compartilhada sobrevive à remoção da cópia privada. Permissões, álbuns, busca, lixeira e restauração funcionam para cada papel. |
| Backup e recuperação | Backup automático em destino adequado; verificação passa; uma cópia é restaurada em ambiente isolado; login, hash dos originais, bibliotecas e espaços conferem depois da restauração. A configuração do host também pode ser reconstruída. |
| Atualização e retorno | Dados de uma versão anterior passam pela migração; se a atualização falhar, há um caminho ensaiado para voltar à versão e aos dados anteriores. |
| Operação | O serviço volta após reinício do host, fica acessível pela tailnet, mostra saúde e logs úteis, e uma falha de backup ou falta de espaço é detectável. |

## Estado do ensaio de recuperação

O ensaio descartável cria um espaço no esquema 2, faz backup pelo código real,
restaura em diretórios temporários e inicia o servidor atual. A leitura dos
álbuns migra o catálogo para o esquema 3; o teste confere que o item e seus
bytes continuam acessíveis, junto com login das duas contas e isolamento das
bibliotecas. Isso valida a migração da aplicação com dados sintéticos. Não
substitui o teste de Docker, upgrade do Compose nem uma restauração ensaiada a
partir de uma cópia dos dados reais.

## Verificação antes de liberar

```bash
python3 -m pytest -q tests/test_instance_backup.py tests/test_backup_scheduler.py
python3 -m pytest -q tests/test_disposable_recovery.py
./scripts/test_upgrade_recovery.sh
./scripts/test_release.sh
```

O ensaio de atualização exige Docker e usa apenas dados sintéticos: constrói
`HEAD` e uma cópia de `HEAD` com as mudanças locais rastreadas, inicia as duas
versões, verifica backup e restauração e confirma o login, isolamento e hashes
após a migração. Em devcontainer, `IRIS_DOCKER_HOST_WORKSPACE` deve apontar para
o caminho do workspace visível ao daemon Docker.

O smoke test de release exige Docker Compose. Ele deve recusar uma árvore Git
com mudanças staged, unstaged ou arquivos não rastreados, porque constrói a
versão registrada em `HEAD`.
