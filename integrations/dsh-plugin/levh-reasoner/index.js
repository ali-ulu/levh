/**
 * LEVH Reasoner — Host plugin for DeepSeek Harness (Cordis bundle).
 *
 * Registers agent tools that add structured decision-making, advisory guard
 * checks, and memory-grounded reasoning on top of a local LEVH API
 * (default http://127.0.0.1:8000, /api/v1/*).
 *
 * Deliberately dependency-free: the profile-installed bundle resolves modules
 * from its real path, so shipped @deepseek-ai packages are not imported here.
 * Tool parameters and output schemas are standard JSON Schema (object root,
 * `required` as an array of strings).
 */
const name = "levh-reasoner";
const inject = ["tools"];

/**
 * Deployment configuration is read defensively from the bundle row's `config`
 * (no schema dependency; unknown fields and absences fall back to defaults).
 * Tunables: baseUrl, defaultProject, agentName, timeoutMs.
 */
function readConfig(raw) {
	const c = raw && typeof raw === "object" ? raw : {};
	return {
		baseUrl: typeof c.baseUrl === "string" && c.baseUrl ? c.baseUrl : "http://127.0.0.1:8000",
		defaultProject: typeof c.defaultProject === "string" ? c.defaultProject : "",
		agentName: typeof c.agentName === "string" && c.agentName ? c.agentName : "dsh-levh-agent",
		timeoutMs: Number.isInteger(c.timeoutMs) ? c.timeoutMs : 8000,
		token: typeof c.token === "string" ? c.token : "",
	};
}

/** Single JSON round-trip against LEVH with a hard timeout; fails loud. */
async function levh(cfg, path, { method = "POST", body, query } = {}) {
	let qs = "";
	if (query) {
		const params = new URLSearchParams();
		for (const [key, value] of Object.entries(query)) {
			if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
		}
		qs = params.toString() ? `?${params.toString()}` : "";
	}
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);
	try {
	/** No regex here: CodeQL flags polynomial-ReDoS patterns on plugin-fed
	 * strings; a linear trim is equivalent to `\/+$`. */
	let base = cfg.baseUrl;
	while (base.endsWith("/")) base = base.slice(0, -1);
	const response = await fetch(base + path + qs, {
			method,
			headers: {
				...(body === undefined ? {} : { "content-type": "application/json" }),
				...(cfg.token ? { authorization: `Bearer ${cfg.token}` } : {}),
			},
			body: body === undefined ? undefined : JSON.stringify(body),
			signal: controller.signal,
		});
		const text = await response.text();
		if (!response.ok) throw new Error(`LEVH ${response.status} ${response.statusText}: ${text.slice(0, 400)}`);
		return text ? JSON.parse(text) : {};
	} catch (error) {
		if (error && error.name === "AbortError") throw new Error(`LEVH request timed out after ${cfg.timeoutMs}ms: ${path}`);
		throw error;
	} finally {
		clearTimeout(timer);
	}
}

function round2(value) {
	return Math.round(value * 100) / 100;
}

/** Linear-scan slug (no regex): agent input is uncontrolled, and CodeQL
 * flags polynomial-ReDoS patterns on it. Same output as the old
 * lowercase/NFKD/collapse/trim pipeline. */
function slugKey(text) {
	let out = "";
	let lastDash = false;
	for (const ch of String(text).toLowerCase().normalize("NFKD")) {
		const ok = (ch >= "a" && ch <= "z") || (ch >= "0" && ch <= "9");
		if (ok) {
			out += ch;
			lastDash = false;
		} else if (!lastDash && out.length > 0) {
			out += "-";
			lastDash = true;
		}
	}
	while (out.endsWith("-")) out = out.slice(0, -1);
	out = out.slice(0, 64);
	while (out.endsWith("-")) out = out.slice(0, -1);
	return out || "decision";
}

/** Deterministic weighted scoring: the judgment is the agent's, the math is not.
 * Arguments arrive frozen, so validation builds normalized local copies and
 * never mutates the model's input. */
function validateAndScore(rawCriteria, rawOptions) {
	if (!Array.isArray(rawCriteria) || rawCriteria.length === 0) throw new Error("invalid decision: `criteria` must be a non-empty array");
	if (!Array.isArray(rawOptions) || rawOptions.length < 2) throw new Error("invalid decision: `options` needs at least 2 entries");
	const seenCriteria = new Set();
	const criteria = rawCriteria.map((criterion) => {
		if (!criterion || typeof criterion.name !== "string" || criterion.name.trim() === "") {
			throw new Error("invalid decision: every criterion needs a non-empty `name`");
		}
		if (seenCriteria.has(criterion.name)) throw new Error(`invalid decision: duplicate criterion ${criterion.name}`);
		const weight = Number(criterion.weight);
		if (!Number.isFinite(weight) || weight <= 0) {
			throw new Error(`invalid decision: criterion ${criterion.name} needs a finite weight > 0`);
		}
		seenCriteria.add(criterion.name);
		return { name: criterion.name, weight };
	});
	const seenOptions = new Set();
	const options = rawOptions.map((option) => {
		if (!option || typeof option.name !== "string" || option.name.trim() === "") {
			throw new Error("invalid decision: every option needs a non-empty `name`");
		}
		if (seenOptions.has(option.name)) throw new Error(`invalid decision: duplicate option ${option.name}`);
		if (!option.scores || typeof option.scores !== "object") {
			throw new Error(`invalid decision: option ${option.name} needs a \`scores\` object keyed by criterion`);
		}
		const scores = {};
		for (const criterion of criteria) {
			const score = option.scores[criterion.name];
			if (score === undefined) {
				throw new Error(`invalid decision: option ${option.name} is missing a score for criterion ${criterion.name}`);
			}
			const value = Number(score);
			if (!Number.isFinite(value) || value < 0 || value > 10) {
				throw new Error(`invalid decision: score ${criterion.name}/${option.name} must be a number in [0, 10]`);
			}
			scores[criterion.name] = value;
		}
		seenOptions.add(option.name);
		return { name: option.name, scores };
	});
	const totalWeight = criteria.reduce((sum, criterion) => sum + criterion.weight, 0);
	const rankWith = (activeCriteria) => {
		const activeWeight = activeCriteria.reduce((sum, criterion) => sum + criterion.weight, 0);
		return options
			.map((option) => {
				const weighted = activeCriteria.reduce((sum, criterion) => sum + option.scores[criterion.name] * criterion.weight, 0);
				return { name: option.name, weighted: round2(weighted), normalized: activeWeight > 0 ? round2(weighted / activeWeight) : 0 };
			})
			.sort((a, b) => b.normalized - a.normalized || a.name.localeCompare(b.name));
	};
	const ranking = rankWith(criteria);
	const winner = ranking[0].name;
	const margin = ranking.length > 1 ? round2(ranking[0].normalized - ranking[1].normalized) : ranking[0].normalized;
	const sensitivity = [];
	for (const criterion of criteria) {
		const without = rankWith(criteria.filter((c) => c.name !== criterion.name));
		if (without[0].name !== winner) {
			sensitivity.push(`removing "${criterion.name}" flips the winner to "${without[0].name}"`);
		}
	}
	return { ranking, totalWeight: round2(totalWeight), winner, margin, sensitivity };
}

const rankingRowSchema = {
	type: "object",
	additionalProperties: false,
	required: ["name", "weighted", "normalized"],
	properties: {
		name: { type: "string" },
		weighted: { type: "number" },
		normalized: { type: "number" },
	},
};

function makeDecideTool(cfg) {
	return {
		name: "levh_decide",
		description:
			"Make a structured, auditable decision: score options against weighted criteria (deterministic math), get the ranking, margin, and a sensitivity check, and record the decision to LEVH so a conflicting decision by another agent is flagged (contested). Use before committing to any non-obvious choice.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["question", "options", "criteria"],
			properties: {
				question: { type: "string", description: "The decision to make, phrased as a question." },
				options: {
					type: "array",
					description: "At least 2 candidate options.",
					items: {
						type: "object",
						additionalProperties: false,
						required: ["name", "scores"],
						properties: {
							name: { type: "string", description: "Option name." },
							scores: {
								type: "object",
								additionalProperties: true,
								description: "Map of criterion name to score in [0, 10] for this option. Every criterion must be scored.",
							},
						},
					},
				},
				criteria: {
					type: "array",
					description: "Decision criteria with positive weights.",
					items: {
						type: "object",
						additionalProperties: false,
						required: ["name", "weight"],
						properties: {
							name: { type: "string", description: "Criterion name, referenced by every option's scores." },
							weight: { type: "number", description: "Positive weight; scale is relative." },
						},
					},
				},
				rationale: { type: "string", description: "Qualitative reasoning recorded with the decision." },
				project: { type: "string", description: "LEVH project scope; empty uses the configured defaultProject." },
				decision_key: { type: "string", description: "Stable key for this decision topic; defaults to a slug of the question." },
				record: { type: "boolean", description: "Record to LEVH (default true). Set false for a dry-run ranking." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["winner", "ranking", "margin", "sensitivity", "statement", "levh"],
				properties: {
					winner: rankingRowSchema,
					ranking: { type: "array", items: rankingRowSchema },
					margin: { type: "number" },
					sensitivity: { type: "array", items: { type: "string" } },
					statement: { type: "string" },
					levh: {
						type: "object",
						additionalProperties: true,
						description: "Recording outcome: recorded, contested, conflicts count, optional error.",
					},
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						`LEVH decision: ${value.winner.name} (${value.winner.normalized}/10, margin ${value.margin}). ` +
						`Ranking: ${value.ranking.map((row, index) => `${index + 1}. ${row.name} ${row.normalized}`).join(", ")}. ` +
						(value.sensitivity.length ? `Sensitivity: ${value.sensitivity.join("; ")}. ` : "Ranking stable across single-criterion removal. ") +
						(value.levh.recorded
							? value.levh.contested
								? `LEVH flagged this decision CONTESTED against ${value.levh.conflicts} conflicting decision(s) — review before acting.`
								: "Recorded in LEVH."
							: value.levh.error
								? `Not recorded: ${value.levh.error}`
								: "Not recorded."),
				},
			],
		},
		timeoutMs: cfg.timeoutMs + 2000,
		presentCall: (args) => ({ card: "generic", title: `LEVH karar: ${String(args.question).slice(0, 80)}`, kind: "other", rawInput: args.question }),
		isConcurrencySafe: () => false,
		async execute(args) {
			const { ranking, winner, margin, sensitivity } = validateAndScore(args.criteria, args.options);
			const best = ranking[0];
			const statement = `${args.question} => ${best.name} (${best.normalized}/10, margin ${margin})`.slice(0, 480);
			const rationaleParts = [
				`Criteria: ${args.criteria.map((c) => `${c.name}(w${c.weight})`).join(", ")}`,
				`Ranking: ${ranking.map((row, index) => `${index + 1}. ${row.name}: ${row.normalized}/10`).join("; ")}.`,
				sensitivity.length ? `Sensitivity: ${sensitivity.join("; ")}.` : "Ranking is stable across single-criterion removal.",
			];
			if (args.rationale) rationaleParts.push(String(args.rationale));
			const project = args.project !== undefined ? String(args.project) : cfg.defaultProject;
			const levhOutcome = { recorded: false, contested: false, conflicts: 0 };
			if (args.record !== false) {
				if (!project) {
					levhOutcome.error = "no project scope configured; pass `project` or set defaultProject";
				} else {
					try {
						const result = await levh(cfg, "/api/v1/team/decisions", {
							body: {
								project,
								decision_key: args.decision_key ? String(args.decision_key) : slugKey(args.question),
								statement,
								rationale: rationaleParts.join(" "),
							},
						});
						levhOutcome.recorded = true;
						levhOutcome.created = result.created === true;
						levhOutcome.contested = result.contested === true;
						levhOutcome.conflicts = Array.isArray(result.conflicts) ? result.conflicts.length : 0;
						if (result.decision && typeof result.decision === "object") levhOutcome.decision_id = result.decision.id ?? null;
					} catch (error) {
						levhOutcome.error = String((error && error.message) || error);
					}
				}
			}
			return { winner: best, ranking, margin, sensitivity, statement, levh: levhOutcome };
		},
	};
}

function makeGuardTool(cfg) {
	return {
		name: "levh_guard_check",
		description:
			"Check a proposed action against LEVH guard rules BEFORE running it. Returns warn when a recorded rule overlaps the action; it never hard-blocks, but a warn is an instruction to heed. Use for risky, mutating, or irreversible actions.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["action_text"],
			properties: {
				action_text: { type: "string", description: "What the action would do — the command, the edit, the call." },
				tool_name: { type: "string", description: "Tool the action would run through (Bash, Write, ...)." },
				project: { type: "string", description: "Scope the rules to one project; empty uses the configured defaultProject." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["decision", "reason", "checked_rules", "matched_rules"],
				properties: {
					decision: { type: "string", description: "allow | warn" },
					reason: { type: "string" },
					checked_rules: { type: "integer" },
					matched_rules: { type: "array", items: { type: "object", additionalProperties: true } },
					tool_name: { type: "string" },
					project: { type: "string" },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						`LEVH guard ${String(value.decision).toUpperCase()}: ${value.reason} ` +
						`(checked ${value.checked_rules} rules${Array.isArray(value.matched_rules) && value.matched_rules.length ? `, matched ${value.matched_rules.length}` : ""}).`,
				},
			],
		},
		timeoutMs: cfg.timeoutMs + 2000,
		presentCall: (args) => ({ card: "generic", title: "LEVH guard kontrolü", kind: "other", rawInput: String(args.action_text).slice(0, 120) }),
		isConcurrencySafe: () => true,
		async execute(args) {
			const body = { action_text: String(args.action_text) };
			if (args.tool_name) body.tool_name = String(args.tool_name);
			const project = args.project !== undefined ? String(args.project) : cfg.defaultProject;
			if (project) body.project = project;
			const result = await levh(cfg, "/api/v1/guard/check", { body });
			return {
				decision: result.decision,
				reason: result.reason,
				checked_rules: result.checked_rules,
				matched_rules: Array.isArray(result.matched_rules) ? result.matched_rules : [],
				tool_name: typeof result.tool_name === "string" ? result.tool_name : "",
				project: typeof result.project === "string" ? result.project : "",
			};
		},
	};
}

function makeAskTool(cfg) {
	return {
		name: "levh_ask_memory",
		description:
			"Ask the shared LEVH memory a question; returns a synthesized, cited answer grounded only in stored memories. Use to ground reasoning in known facts, past decisions, and constraints before acting.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["question"],
			properties: {
				question: { type: "string", description: "The question to answer from memory." },
				project: { type: "string", description: "LEVH project scope; empty uses the configured defaultProject." },
				top_k: { type: "integer", description: "Maximum evidence items to consider (1-20, default 6)." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["question", "answer", "sources"],
				properties: {
					question: { type: "string" },
					answer: { type: "string" },
					sources: { type: "array", items: { type: "object", additionalProperties: true } },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text: `LEVH memory: ${String(value.answer).slice(0, 1200)} (${Array.isArray(value.sources) ? value.sources.length : 0} sources)`,
				},
			],
		},
		timeoutMs: cfg.timeoutMs + 2000,
		presentCall: (args) => ({ card: "generic", title: "LEVH hafıza sorusu", kind: "other", rawInput: String(args.question).slice(0, 120) }),
		isConcurrencySafe: () => true,
		async execute(args) {
			const body = { question: String(args.question), top_k: Number.isInteger(args.top_k) ? Math.min(20, Math.max(1, args.top_k)) : 6 };
			const project = args.project !== undefined ? String(args.project) : cfg.defaultProject;
			if (project) body.project = project;
			return levh(cfg, "/api/v1/ask", { body });
		},
	};
}

function makeConflictsTool(cfg) {
	return {
		name: "levh_decision_conflicts",
		description:
			"Scan LEVH for unresolved free-text decision conflicts between differently-keyed team decisions in a project. Use after recording decisions, or before relying on shared decisions.",
		parameters: {
			type: "object",
			additionalProperties: false,
			properties: {
				project: { type: "string", description: "LEVH project scope; empty uses the configured defaultProject." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["new_candidates", "pairs_examined", "open_total", "stale_resolved"],
				properties: {
					new_candidates: { type: "integer" },
					pairs_examined: { type: "integer" },
					open_total: { type: "integer" },
					stale_resolved: { type: "integer" },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text: `LEVH decision conflicts: ${value.open_total} open (${value.new_candidates} new candidates from ${value.pairs_examined} pairs, ${value.stale_resolved} stale resolved).`,
				},
			],
		},
		timeoutMs: cfg.timeoutMs + 2000,
		presentCall: () => ({ card: "generic", title: "LEVH karar çelişkisi taraması", kind: "other" }),
		isConcurrencySafe: () => true,
		async execute(args) {
			const project = args.project !== undefined ? String(args.project) : cfg.defaultProject;
			return levh(cfg, "/api/v1/team/decision-conflicts/detect", { query: project ? { project } : undefined });
		},
	};
}

/** Register the four LEVH reasoning tools; the disposer unregisters them all. */
export function apply(ctx, rawConfig) {
	const cfg = readConfig(rawConfig);
	const definitions = [makeDecideTool(cfg), makeGuardTool(cfg), makeAskTool(cfg), makeConflictsTool(cfg)];
	return ctx.effect(() => {
		const disposers = definitions.map((definition) => ctx.tools.register(definition));
		return () => {
			for (const dispose of disposers) dispose();
		};
	});
}

export { inject, name };
