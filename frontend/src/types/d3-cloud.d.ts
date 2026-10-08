// Lightweight d3-cloud type declarations — the official @types/d3-cloud depends on the full d3 package (not installed in the project), so this self-declaration only covers the surface used.
declare module 'd3-cloud' {
  export interface CloudWord {
    text?: string;
    size?: number;
    rotate?: number;
    padding?: number;
    x?: number;
    y?: number;
    [key: string]: unknown;
  }

  export interface CloudLayout<T extends CloudWord> {
    start(): CloudLayout<T>;
    stop(): CloudLayout<T>;
    words(words: T[]): CloudLayout<T>;
    size(size: [number, number]): CloudLayout<T>;
    font(font: string | ((d: T, i: number) => string)): CloudLayout<T>;
    fontSize(size: number | ((d: T, i: number) => number)): CloudLayout<T>;
    rotate(rotate: number | ((d: T, i: number) => number)): CloudLayout<T>;
    padding(padding: number | ((d: T, i: number) => number)): CloudLayout<T>;
    spiral(name: string): CloudLayout<T>;
    random(fn: () => number): CloudLayout<T>;
    on(event: 'end', cb: (words: T[]) => void): CloudLayout<T>;
  }

  export default function cloud<T extends CloudWord = CloudWord>(): CloudLayout<T>;
}
