# Research Summary

## Stack

Use Python 3.11+, the official MCP Python SDK, LanceDB as the embedded vector store, and Sentence Transformers with a lightweight local model for CPU-friendly embeddings.

Використовувати Python 3.11+, офіційний MCP Python SDK, LanceDB як embedded vector store і Sentence Transformers з легкою локальною моделлю для CPU-friendly embeddings.

## Table Stakes

- Local stdio MCP server for Claude Code
- Локальний stdio MCP-сервер для Claude Code
- Markdown indexing with stable chunks
- Markdown-індексація зі stable chunks
- Semantic search with source traceability
- Semantic search із traceability до джерела
- Compressed recall plus on-demand hydration
- Compressed recall плюс on-demand hydration
- Persistent write-back memory
- Персистентний write-back memory

## Watch Out For

- Overcompression without hydration
- Надмірне стиснення без hydration
- Stale indexes after file changes
- Застарілий індекс після змін у файлах
- Installation complexity that breaks the "easy deploy" promise
- Складність інсталяції, яка ламає обіцянку "easy deploy"
- Treating stored notes as trusted instructions
- Спроба трактувати збережені notes як trusted instructions

## Product Inference

TurboQuant should be treated as conceptual guidance for aggressive compression plus selective recovery, not as a literal promise that Claude Code tokens or hosted-model KV cache can be directly quantized by this project.

TurboQuant треба трактувати як концептуальну підказку для агресивного стиснення та вибіркового відновлення, а не як буквальну обіцянку, що цей проєкт зможе напряму квантувати токени Claude Code або KV cache hosted-моделі.
