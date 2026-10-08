// Article body import (frontend version) -- the browser directly supports .txt / .md / .docx.
// PDF / old .doc still need backend parsing; the backend isn't changed at this stage.

import mammoth from 'mammoth';

export interface ImportedDocText {
  text: string;
  filename: string;
}

const TEXT_IMPORT_EXTS = ['.txt', '.md', '.docx'];

function fileExt(name: string): string {
  const m = name.toLowerCase().match(/\.[a-z0-9]+$/);
  return m?.[0] || '';
}

function normalizeImportedText(text: string): string {
  return text.replace(/^﻿/, '').replace(/\r\n?/g, '\n').trim();
}

export const CONTENT_LOCAL_IMPORT_ACCEPT = TEXT_IMPORT_EXTS.join(',');

async function readDocxText(file: File): Promise<string> {
  const arrayBuffer = await file.arrayBuffer();
  const result = await mammoth.extractRawText({ arrayBuffer });
  return normalizeImportedText(result.value || '');
}

export async function readImportableDoc(file: File): Promise<ImportedDocText> {
  const ext = fileExt(file.name);
  let text = '';

  if (ext === '.txt' || ext === '.md') {
    text = normalizeImportedText(await file.text());
  } else if (ext === '.docx') {
    text = await readDocxText(file);
  } else {
    throw new Error('当前前端版支持 .txt / .md / .docx 导入；PDF / 老 .doc 仍需后端接口支持。');
  }

  if (!text) {
    throw new Error('文件里没有可导入的文字');
  }
  return { text, filename: file.name };
}
