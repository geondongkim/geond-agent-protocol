# International Launch Channels

This document extends the Geond launch plan beyond Hacker News and Reddit. It
maps the languages supported by the README selector to practical developer
communities, social-news sites, and editorial channels.

The goal is not to blast the same post everywhere. Each launch should be local,
modest, and specific to the audience. Use the local README when available, keep
the alpha boundary visible, and ask for workflow feedback rather than only stars.

## Channel Map

| Language | Primary channels | Best format | Geond angle | Notes |
| --- | --- | --- | --- | --- |
| English | [Hacker News](https://news.ycombinator.com/), [DEV Community](https://dev.to/), `r/mcp`, `r/LocalLLaMA`, `r/ClaudeAI` | Show HN post, technical article, subreddit-specific discussion | Shared memory, reservations, handoffs, and PostgreSQL-backed evidence for AI coding agents. | Hacker News should stay low-hype and technically precise. DEV is better for tutorial content. |
| Korean | [GeekNews](https://news.hada.io/), GeekNews comments, Korean developer communities after first feedback | Link submission plus Korean comment | 여러 AI 코딩 에이전트가 같은 repo에서 서로의 작업을 덮어쓰지 않도록 shared memory, reservation, handoff를 제공. | GeekNews officially focuses on development, technology, products, open source, and startups. |
| Japanese | [Qiita](https://qiita.com/), [Zenn](https://zenn.dev/), Hatena Bookmark discovery | Technical article in Japanese | 複数の AI coding agent が同じ repo で作業するときの context drift, reservation, handoff. | Qiita and Zenn are article-first. Publish a useful tutorial before asking for attention. |
| Simplified Chinese | [V2EX](https://www.v2ex.com/), Juejin, SegmentFault | Discussion post or technical article | 多个 AI coding agents 在同一 repo 中共享 memory、reservation、handoff 和 review evidence. | V2EX is discussion-first and has strict community norms. Avoid AI-generated-looking promotional text. |
| Spanish | [Menéame](https://www.meneame.net/), [HackniA](https://www.hacknia.com/), Spanish DEV/Reddit communities | Link submission or maker/dev community post | Coordinar agentes de código AI con memoria compartida, reservas y handoffs en local. | Menéame is broad social news; HackniA is closer to makers, devs, sysadmins, and build-in-public. |
| French | [LinuxFr.org](https://linuxfr.org/), French DEV posts, relevant Mastodon/Fediverse circles | Open-source focused write-up | Mémoire partagée, réservations et handoffs pour agents de code AI, avec PostgreSQL local-first. | LinuxFr is best when the post emphasizes free/open-source value and technical detail. |
| German | [heise Developer](https://www.heise.de/developer/), [entwickler.de](https://entwickler.de/), [Golem.de](https://www.golem.de/), German-speaking Reddit/Mastodon communities | Editorial pitch or German technical article | Lokale Koordination für AI coding agents: shared memory, reservations, handoffs, dashboard review. | German channels are more editorial/community-event oriented than HN-like. Lead with practical engineering and privacy. |

## Launch Sequence

1. Publish the English launch first on Hacker News only after the README,
   Quick Start, GIFs, comparison doc, and issue templates are ready.
2. Submit the Korean version to GeekNews the same week, with a short Korean
   comment explaining why the project was built.
3. Publish Japanese and Chinese technical articles after the first English/Korean
   feedback has removed setup friction.
4. Publish Spanish, French, and German posts as localized technical write-ups,
   not as direct translations of the HN post.
5. Track questions and repeat confusion in Geond handoffs or issues, then update
   README/FAQ before the next language launch.

## Localized Positioning Hooks

| Language | Hook |
| --- | --- |
| English | Stop AI coding agents from stepping on each other's work. |
| Korean | 여러 AI 코딩 에이전트가 같은 repo에서 서로의 작업을 덮어쓰지 않게 합니다. |
| Japanese | 複数の AI coding agent が同じ repo で作業しても、文脈と作業予約を共有できます。 |
| Simplified Chinese | 让多个 AI coding agents 在同一个 repo 中共享上下文、预约工作并留下交接证据。 |
| Spanish | Evita que varios agentes de código AI se pisen el trabajo en el mismo repo. |
| French | Empêcher plusieurs agents de code AI de se marcher dessus dans le même dépôt. |
| German | Verhindert, dass mehrere AI coding agents sich im selben Repo gegenseitig überschreiben. |

## GeekNews Draft

Title:

```text
Geond - 여러 AI 코딩 에이전트가 같은 repo에서 협업하도록 돕는 local-first MCP 서버
```

Comment:

```text
Codex, Claude Code, Copilot, Antigravity 같은 도구를 같은 repo에서 함께 쓰면
"무슨 작업이 진행 중인지", "어떤 파일/심볼을 건드리고 있는지", "다음 에이전트가
무엇을 이어받아야 하는지"가 쉽게 끊깁니다.

Geond는 이 문제를 Git 대신 해결하려는 도구가 아니라, Git 주변의 작업 맥락을
저장하는 local-first MCP/CLI 레이어입니다. PostgreSQL/pgvector에 agent memory,
file/symbol reservation, handoff, code graph evidence를 저장하고 dashboard로
review loop를 보여줍니다.

아직 alpha이고 repository-centered workflow가 가장 강합니다. 엔터프라이즈 IAM,
RLS, SaaS hosting은 roadmap입니다. 여러 AI coding tool을 동시에 써보신 분들이
reservation/handoff 모델이 실제 workflow에 맞는지 피드백해주시면 특히 도움이 됩니다.
```

## Per-Language Content Plan

| Language | First artifact | Second artifact |
| --- | --- | --- |
| English | Show HN + first comment | DEV article: "Why AI coding agents step on each other's work" |
| Korean | GeekNews link + Korean comment | Korean blog note based on README.ko.md and the pair-coding GIF |
| Japanese | Zenn article: setup + pair-coding workflow | Qiita article: MCP client config and handoff/reservation commands |
| Simplified Chinese | V2EX discussion asking for workflow feedback | Juejin or SegmentFault article if the discussion shows interest |
| Spanish | HackniA maker/dev post | Menéame link only after there is a useful Spanish article |
| French | LinuxFr journal/news-style post | DEV article in French with local-first and open-source angle |
| German | German technical article or Mastodon thread | Editorial pitch to heise Developer/entwickler.de if traction appears |

## Guardrails

- Do not post machine-translated copy without a human or fluent reviewer pass.
- Do not imply Geond has country-specific support beyond translated README files.
- Do not imply Geond is an orchestrator, SaaS platform, or automatic merge
  conflict resolver.
- Do not post patent-sensitive details or private validation artifacts.
- Do not cross-post the same day to every community. Sequence launches so
  repeated questions improve the next post.
- For V2EX and similar communities, write as a builder asking for technical
  critique, not as a marketer.
