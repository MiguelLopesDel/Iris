# Documentação do Iris

Este índice separa decisões de produto, comportamento implementado, operação e
material de pesquisa. Uma proposta ou plano não significa que o recurso já
exista; confira também os contratos e testes da área antes de alterar código.

## Produto e arquitetura

- [Visão de produto](product-vision.md) — decisões confirmadas sobre contas,
  bibliotecas, compartilhamento e experiência esperada.
- [Plano de implementação](implementation-roadmap.md) — estado observado,
  lacunas e sequência de entrega; não é garantia de capacidade.
- [Arquitetura de sincronização](photo-video-sync-architecture.md) e
  [contrato Android](android-sync-contract.md) — fluxo e invariantes de backup.
- [Contrato de migração e recuperação](migration-and-recovery-contract.md) —
  requisitos para alterações de persistência e recuperação.
- [Arquitetura de deduplicação](dedup-architecture.md) — identidade de mídia e
  garantias de deduplicação.
- [Perfis de dependências](dependency-profiles.md) — CPU, CUDA e desenvolvimento.

## Desenvolver e validar

- [Processo de desenvolvimento e de lançamento](process.md) — ambientes
  (desenvolvimento, homologação, produção), o que é um commit, um PR e uma tag,
  onde entra cada teste e os invariantes do projeto.
- [Lições técnicas de campo](engineering-lessons.md) — regras aprendidas com
  erros reais de memória, cache e ranqueamento.
- [Mapa para novos contribuidores](developer-onboarding.md) — caminhos atuais
  de inicialização, galeria, sincronização e backups.
- [Contribuir e configurar o ambiente](../CONTRIBUTING.md).
- [Testes e diagnóstico](testing.md) — verificações rápidas, smoke tests e
  release.
- [Fluxo de testes Android](android-testing-workflow.md) — ciclo rápido,
  app de laboratório isolado, automação de UI e uso seguro de aparelho físico.
- [Testes de desempenho](performance-testing.md) — ferramentas, cargas e limites
  das medições.
- [Prontidão do piloto](pilot-readiness.md) — validações operacionais pendentes.

## Instalar e operar

- [Implantação do servidor](server-deployment.md) — instalação, rede, atualização
  e preservação de dados.

## Pesquisa e decisões históricas

- [ADR 0001: servidor como autoridade da sincronização](adr/0001-server-authoritative-library-sync.md).
- [Padrões de sincronização de fotos](research/photo-sync-patterns.md),
  [Google Fotos/iCloud](research/consumer-photo-sync-google-icloud.md) e
  [primitivas de upload em lote](research/upload-batching-primitives.md).
- [Avaliação de modelos](model-evaluation.md) registra metodologia e resultados
  experimentais; verifique a data e o hardware antes de reutilizar conclusões.
