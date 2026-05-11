export function buildAnswer(prompt: string): string {
  const normalized = normalize(prompt);
  return `Answer: ${normalized}`;
}

const normalize = (value: string): string => value.trim();

export class SessionReporter {
  report(prompt: string): string {
    return buildAnswer(prompt);
  }
}
