/**
 * Cognition Engine — Host plugin for DeepSeek Harness (Cordis bundle).
 *
 * Deterministic cognitive tools for agents: forward-chaining deduction with
 * contradiction detection, Bayesian hypothesis testing, pruned thought-tree
 * deliberation, decision-making under uncertainty, and a post-decision
 * reflection loop that can store the lesson back into LEVH.
 *
 * The division of labor is deliberate: the agent supplies the judgment
 * (hypotheses, scores, ideas), the plugin supplies the reliable computation
 * (chain derivation, Bayes, expected value, pruning). Deliberately
 * dependency-free and regex-free (profile bundles resolve from their real
 * path; CodeQL flags polynomial-ReDoS patterns on plugin-fed strings).
 */
const name = "cog-engine";
const inject = ["tools"];

/** Defensive config read; `baseUrl`/`token` are only used by `cog_reflect`. */
function readConfig(raw) {
	const c = raw && typeof raw === "object" ? raw : {};
	return {
		baseUrl: typeof c.baseUrl === "string" && c.baseUrl ? c.baseUrl : "http://127.0.0.1:8000",
		defaultProject: typeof c.defaultProject === "string" ? c.defaultProject : "",
		timeoutMs: Number.isInteger(c.timeoutMs) ? c.timeoutMs : 8000,
		token: typeof c.token === "string" ? c.token : "",
	};
}

async function postJson(cfg, path, body) {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), cfg.timeoutMs);
	try {
		let base = cfg.baseUrl;
		while (base.endsWith("/")) base = base.slice(0, -1);
		const response = await fetch(base + path, {
			method: "POST",
			headers: {
				"content-type": "application/json",
				...(cfg.token ? { authorization: `Bearer ${cfg.token}` } : {}),
			},
			body: JSON.stringify(body),
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

function round4(value) {
	return Math.round(value * 10000) / 10000;
}

/** Whitespace/underscore-free lowercase words, without regex. */
function words(text) {
	const out = [];
	let cur = "";
	for (const ch of String(text).toLowerCase()) {
		const ok = (ch >= "a" && ch <= "z") || (ch >= "0" && ch <= "9");
		if (ok) {
			cur += ch;
		} else if (cur) {
			out.push(cur);
			cur = "";
		}
	}
	if (cur) out.push(cur);
	return out;
}

function jaccard(a, b) {
	const setA = new Set(a);
	const setB = new Set(b);
	if (!setA.size || !setB.size) return 0;
	let inter = 0;
	for (const item of setA) if (setB.has(item)) inter++;
	return inter / (setA.size + setB.size - inter);
}

/** First finite number appearing in free text, or null. */
function firstNumber(text) {
	let cur = "";
	for (const ch of String(text)) {
		const ok = (ch >= "0" && ch <= "9") || ch === "." || ch === "-" || ch === "+";
		if (ok) {
			cur += ch;
		} else if (cur) {
			const value = Number(cur);
			if (Number.isFinite(value)) return value;
			cur = "";
		}
	}
	if (cur) {
		const value = Number(cur);
		if (Number.isFinite(value)) return value;
	}
	return null;
}

function scoreInRange(value, label) {
	const n = Number(value);
	if (!Number.isFinite(n) || n < 0 || n > 10) throw new Error(`invalid input: ${label} must be a number in [0, 10]`);
	return n;
}

/* ------------------------------- cog_deduce ------------------------------- */

function makeDeduceTool() {
	return {
		name: "cog_deduce",
		description:
			"Run forward-chaining deduction over facts and if-then rules: derives all entailments to a fixpoint with a step-by-step proof trace, and reports contradictions (both X and 'not X' derivable). Deterministic logic; use to check whether a conclusion actually follows, or to map the full consequence set before deciding.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["facts", "rules"],
			properties: {
				facts: { type: "array", items: { type: "string" }, description: "Known facts as literals. Negations use the literal prefix 'not ' (e.g. 'deploy done', 'not tests green')." },
				rules: {
					type: "array",
					description: "If-then rules; a rule fires when every 'if' literal is present. Use 'not ' for negative literals.",
					items: {
						type: "object",
						additionalProperties: false,
						required: ["if", "then"],
						properties: {
							id: { type: "string", description: "Optional rule name shown in the proof trace." },
							if: { type: "array", items: { type: "string" }, description: "Required literals; empty array means the rule fires immediately." },
							then: { type: "string", description: "Derived literal." },
						},
					},
				},
				max_steps: { type: "integer", description: "Chaining pass cap (default 25)." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["all_facts", "derived", "contradictions", "steps_used", "fixpoint"],
				properties: {
					all_facts: { type: "array", items: { type: "string" } },
					derived: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["fact", "rule", "from", "step"],
							properties: {
								fact: { type: "string" },
								rule: { type: "string" },
								from: { type: "string" },
								step: { type: "integer" },
							},
						},
					},
					contradictions: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["positive", "negative"],
							properties: { positive: { type: "string" }, negative: { type: "string" } },
						},
					},
					steps_used: { type: "integer" },
					fixpoint: { type: "boolean" },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						`cog_deduce: ${value.all_facts.length} facts (${value.derived.length} derived) in ${value.steps_used} passes` +
						(value.fixpoint ? ", fixpoint reached" : ", STOPPED at the step cap — results may be incomplete") +
						(value.contradictions.length
							? `; CONTRADICTIONS: ${value.contradictions.map((c) => `${c.positive} vs ${c.negative}`).join("; ")}`
							: "; consistent") +
						".",
				},
			],
		},
		presentCall: () => ({ card: "generic", title: "cog_deduce: mantık zinciri", kind: "other" }),
		isConcurrencySafe: () => true,
		async execute(args) {
			const rawFacts = Array.isArray(args.facts) ? args.facts : null;
			const rawRules = Array.isArray(args.rules) ? args.rules : null;
			if (!rawFacts) throw new Error("invalid input: `facts` must be an array of strings");
			if (!rawRules || rawRules.length === 0) throw new Error("invalid input: `rules` must be a non-empty array");
			const maxSteps = Number.isInteger(args.max_steps) && args.max_steps > 0 ? Math.min(args.max_steps, 200) : 25;
			const set = new Set(rawFacts.map((f) => String(f).trim()).filter((f) => f.length > 0));
			const rules = rawRules.map((rule, index) => {
				if (!rule || !Array.isArray(rule.if) || typeof rule.then !== "string" || rule.then.trim() === "") {
					throw new Error(`invalid input: rules[${index}] needs an 'if' array and a non-empty 'then'`);
				}
				return { id: typeof rule.id === "string" && rule.id ? rule.id : `rule[${index}]`, if: rule.if.map((lit) => String(lit).trim()), then: rule.then.trim() };
			});
			const derivations = [];
			const firedRules = [];
			let fixpoint = false;
			let passes = 0;
			for (; passes < maxSteps; passes++) {
				let firedThisPass = false;
				for (const rule of rules) {
					const missing = rule.if.filter((lit) => !set.has(lit));
					if (missing.length === 0 && !set.has(rule.then)) {
						set.add(rule.then);
						firedThisPass = true;
						firedRules.push(rule.id);
						derivations.push({ fact: rule.then, rule: rule.id, from: rule.if.join(" & ") || "(no premises)", step: passes + 1 });
					}
				}
				if (!firedThisPass) {
					fixpoint = true;
					break;
				}
			}
			const allFacts = Array.from(set).sort();
			const contradictions = [];
			for (const fact of allFacts) {
				if (fact.startsWith("not ") && set.has(fact.slice(4))) {
					contradictions.push({ positive: fact.slice(4), negative: fact });
				}
			}
			return { all_facts: allFacts, derived: derivations, contradictions, steps_used: passes + (fixpoint ? 0 : 1), fixpoint };
		},
	};
}

/* ----------------------------- cog_hypothesize ---------------------------- */

function makeHypothesizeTool() {
	return {
		name: "cog_hypothesize",
		description:
			"Test competing hypotheses against evidence with a Bayesian update: supply hypotheses with priors and, per observation, a likelihood P(observation|hypothesis); get posteriors, the leader, weak (non-discriminating) evidence, and which single observation would flip the ranking. Use for diagnosis, root-cause work, and abductive reasoning.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["hypotheses", "evidence"],
			properties: {
				hypotheses: {
					type: "array",
					description: "At least 2 competing hypotheses.",
					items: {
						type: "object",
						additionalProperties: false,
						required: ["name", "prior"],
						properties: {
							name: { type: "string" },
							prior: { type: "number", description: "Prior probability in [0, 1]; they should sum to 1 (they are normalized otherwise)." },
						},
					},
				},
				evidence: {
					type: "array",
					description: "Observations with a likelihood per hypothesis; every hypothesis must be scored for every observation.",
					items: {
						type: "object",
						additionalProperties: false,
						required: ["observation", "likelihoods"],
						properties: {
							observation: { type: "string" },
							likelihoods: {
								type: "object",
								additionalProperties: true,
								description: "Map of hypothesis name to P(observation|hypothesis) in [0, 1].",
							},
						},
					},
				},
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["leader", "posteriors", "weak_evidence", "flips", "vacuous"],
				properties: {
					leader: { type: "string" },
					posteriors: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["name", "prior", "posterior"],
							properties: { name: { type: "string" }, prior: { type: "number" }, posterior: { type: "number" } },
						},
					},
					weak_evidence: { type: "array", items: { type: "string" } },
					flips: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["observation", "leader_without"],
							properties: { observation: { type: "string" }, leader_without: { type: "string" } },
						},
					},
					vacuous: { type: "boolean", description: "True when the evidence is incompatible with every hypothesis." },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						(value.vacuous
							? "cog_hypothesize: the evidence is incompatible with EVERY hypothesis (all likelihoods multiply to zero) — check the likelihoods. "
							: `cog_hypothesize: leader "${value.leader}" (${value.posteriors[0].posterior}); ranking: ${value.posteriors.map((p) => `${p.name} ${p.posterior}`).join(", ")}. `) +
						(value.weak_evidence.length ? `Weak evidence: ${value.weak_evidence.join("; ")}. ` : "All evidence discriminates. ") +
						(value.flips.length ? `Flips: ${value.flips.map((f) => `without "${f.observation}" the leader becomes "${f.leader_without}"`).join("; ")}.` : "Ranking robust to single-evidence removal."),
				},
			],
		},
		presentCall: () => ({ card: "generic", title: "cog_hypothesize: hipotez testi", kind: "other" }),
		isConcurrencySafe: () => true,
		async execute(args) {
			const rawHypotheses = Array.isArray(args.hypotheses) ? args.hypotheses : null;
			const rawEvidence = Array.isArray(args.evidence) ? args.evidence : null;
			if (!rawHypotheses || rawHypotheses.length < 2) throw new Error("invalid input: `hypotheses` needs at least 2 entries");
			if (!rawEvidence || rawEvidence.length === 0) throw new Error("invalid input: `evidence` must be a non-empty array");
			const names = [];
			let priorSum = 0;
			const priors = rawHypotheses.map((h) => {
				if (!h || typeof h.name !== "string" || h.name.trim() === "") throw new Error("invalid input: every hypothesis needs a non-empty name");
				if (names.includes(h.name)) throw new Error(`invalid input: duplicate hypothesis ${h.name}`);
				const prior = Number(h.prior);
				if (!Number.isFinite(prior) || prior < 0 || prior > 1) throw new Error(`invalid input: prior of ${h.name} must be in [0, 1]`);
				names.push(h.name);
				priorSum += prior;
				return { name: h.name, prior };
			});
			const normalizedPriors = Math.abs(priorSum - 1) > 0.001;
			if (normalizedPriors) {
				if (priorSum <= 0) throw new Error("invalid input: priors sum to 0");
				for (const p of priors) p.prior = p.prior / priorSum;
			}
			const evidence = rawEvidence.map((item, index) => {
				if (!item || typeof item.observation !== "string" || item.observation.trim() === "" || !item.likelihoods || typeof item.likelihoods !== "object") {
					throw new Error(`invalid input: evidence[${index}] needs an observation and a likelihoods object`);
				}
				const likelihoods = {};
				for (const hypothesis of names) {
					const like = Number(item.likelihoods[hypothesis]);
					if (!Number.isFinite(like) || like < 0 || like > 1) {
						throw new Error(`invalid input: evidence "${item.observation}" needs a likelihood in [0, 1] for hypothesis "${hypothesis}"`);
					}
					likelihoods[hypothesis] = like;
				}
				return { observation: item.observation, likelihoods };
			});
			const weightsFor = (skipIndex) =>
				priors.map((p) => ({
					name: p.name,
					weight: evidence.reduce((acc, item, index) => (index === skipIndex ? acc : acc * item.likelihoods[p.name]), p.prior),
				}));
			const allWeights = weightsFor(-1);
			const totalWeight = allWeights.reduce((sum, w) => sum + w.weight, 0);
			if (totalWeight <= 0) {
				return {
					leader: names[0],
					posteriors: priors.map((p) => ({ name: p.name, prior: round4(p.prior), posterior: round4(p.prior) })),
					weak_evidence: [],
					flips: [],
					vacuous: true,
				};
			}
			const posteriors = allWeights.map((w) => {
				const prior = priors.find((p) => p.name === w.name).prior;
				return { name: w.name, prior: round4(prior), posterior: round4(w.weight / totalWeight) };
			});
			const ranking = [...posteriors].sort((a, b) => b.posterior - a.posterior || a.name.localeCompare(b.name));
			const leader = ranking[0].name;
			const weakEvidence = [];
			for (const item of evidence) {
				const likes = names.map((n) => item.likelihoods[n]);
				if (Math.max(...likes) - Math.min(...likes) < 0.15) weakEvidence.push(item.observation);
			}
			const flips = [];
			for (let index = 0; index < evidence.length; index++) {
				const without = weightsFor(index);
				const totalWithout = without.reduce((sum, w) => sum + w.weight, 0);
				if (totalWithout <= 0) continue;
				const rankedWithout = without
					.map((w) => ({ name: w.name, posterior: w.weight / totalWithout }))
					.sort((a, b) => b.posterior - a.posterior || a.name.localeCompare(b.name));
				if (rankedWithout[0].name !== leader) flips.push({ observation: evidence[index].observation, leader_without: rankedWithout[0].name });
			}
			return { leader, posteriors: ranking, weak_evidence: weakEvidence, flips, vacuous: false };
		},
	};
}

/* ----------------------------- cog_deliberate ----------------------------- */

function makeDeliberateTool() {
	return {
		name: "cog_deliberate",
		description:
			"Deliberate like a pruned thought tree: supply idea branches generated under different frames with scores (novelty/feasibility/impact or your own axes); the tool computes weighted composites, prunes weak and near-duplicate branches, and ranks the survivors. Use for design choices, plans, and divergent-then-convergent thinking.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["problem", "branches"],
			properties: {
				problem: { type: "string", description: "The problem being deliberated." },
				branches: {
					type: "array",
					description: "At least 3 idea branches; generate them under DIFFERENT frames.",
					items: {
						type: "object",
						additionalProperties: false,
						required: ["frame", "idea", "scores"],
						properties: {
							frame: { type: "string", description: "Cognitive frame the idea came from (e.g. 'cheapest', 'safest', 'fastest to learn')." },
							idea: { type: "string" },
							scores: { type: "object", additionalProperties: true, description: "Score map in [0, 10]; every branch must score every weighted key." },
						},
					},
				},
				weights: { type: "object", additionalProperties: true, description: "Optional key->weight map; defaults to weight 1 for every score key used." },
				prune_below: { type: "number", description: "Composite below this is pruned (default 5)." },
				keep_top: { type: "integer", description: "Survivors kept after pruning (default 3)." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["problem", "survivors", "pruned", "frames_used"],
				properties: {
					problem: { type: "string" },
					survivors: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["frame", "idea", "composite"],
							properties: { frame: { type: "string" }, idea: { type: "string" }, composite: { type: "number" } },
						},
					},
					pruned: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["idea", "reason"],
							properties: { idea: { type: "string" }, reason: { type: "string" } },
						},
					},
					frames_used: { type: "integer" },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						`cog_deliberate: ${value.survivors.length} survivor(s) from ${value.pruned.length + value.survivors.length} branches (${value.frames_used} frames). ` +
						`Top: ${value.survivors.map((s, i) => `${i + 1}. [${s.frame}] ${s.idea.slice(0, 60)} (${s.composite})`).join(" | ")}.` +
						(value.pruned.length ? ` Pruned: ${value.pruned.map((p) => `"${p.idea.slice(0, 40)}" (${p.reason})`).join("; ")}.` : ""),
				},
			],
		},
		presentCall: (args) => ({ card: "generic", title: "cog_deliberate: düşünme ağacı", kind: "other", rawInput: String(args.problem).slice(0, 120) }),
		isConcurrencySafe: () => true,
		async execute(args) {
			if (typeof args.problem !== "string" || args.problem.trim() === "") throw new Error("invalid input: `problem` must be a non-empty string");
			const rawBranches = Array.isArray(args.branches) ? args.branches : null;
			if (!rawBranches || rawBranches.length < 3) throw new Error("invalid input: `branches` needs at least 3 entries");
			const pruneBelow = Number.isFinite(Number(args.prune_below)) ? Number(args.prune_below) : 5;
			const keepTop = Number.isInteger(args.keep_top) && args.keep_top > 0 ? args.keep_top : 3;
			const weightsArg = args.weights && typeof args.weights === "object" && !Array.isArray(args.weights) ? args.weights : null;
			let keys;
			const weights = {};
			if (weightsArg && Object.keys(weightsArg).length > 0) {
				keys = Object.keys(weightsArg);
				for (const key of keys) {
					const w = Number(weightsArg[key]);
					if (!Number.isFinite(w) || w <= 0) throw new Error(`invalid input: weight of "${key}" must be a finite number > 0`);
					weights[key] = w;
				}
			} else {
				keys = [];
				for (const branch of rawBranches) {
					if (branch && branch.scores && typeof branch.scores === "object") {
						for (const key of Object.keys(branch.scores)) if (!keys.includes(key)) keys.push(key);
					}
				}
				if (keys.length === 0) throw new Error("invalid input: branches carry no score keys");
				for (const key of keys) weights[key] = 1;
			}
			const weightSum = keys.reduce((sum, key) => sum + weights[key], 0);
			const branches = rawBranches.map((branch, index) => {
				if (!branch || typeof branch.frame !== "string" || branch.frame.trim() === "" || typeof branch.idea !== "string" || branch.idea.trim() === "") {
					throw new Error(`invalid input: branches[${index}] needs non-empty frame and idea`);
				}
				if (!branch.scores || typeof branch.scores !== "object") throw new Error(`invalid input: branches[${index}] needs a scores object`);
				const scores = {};
				for (const key of keys) {
					if (branch.scores[key] === undefined) throw new Error(`invalid input: branch "${branch.idea.slice(0, 40)}" is missing score "${key}"`);
					scores[key] = scoreInRange(branch.scores[key], `score "${key}" of branch "${branch.idea.slice(0, 40)}"`);
				}
				const composite = keys.reduce((sum, key) => sum + weights[key] * scores[key], 0) / weightSum;
				return { frame: branch.frame, idea: branch.idea, composite: round4(composite) };
			});
			const ranked = [...branches].sort((a, b) => b.composite - a.composite);
			const survivors = [];
			const pruned = [];
			for (const branch of ranked) {
				if (branch.composite < pruneBelow) {
					pruned.push({ idea: branch.idea, reason: `below threshold ${pruneBelow} (composite ${branch.composite})` });
					continue;
				}
				let duplicateOf = null;
				for (const kept of survivors) {
					if (jaccard(words(branch.idea), words(kept.idea)) >= 0.8) {
						duplicateOf = kept.idea;
						break;
					}
				}
				if (duplicateOf) {
					pruned.push({ idea: branch.idea, reason: `near-duplicate of "${duplicateOf.slice(0, 60)}"` });
					continue;
				}
				if (survivors.length < keepTop) survivors.push(branch);
				else pruned.push({ idea: branch.idea, reason: `outside top ${keepTop}` });
			}
			const framesUsed = new Set(branches.map((b) => b.frame)).size;
			return { problem: args.problem, survivors, pruned, frames_used: framesUsed };
		},
	};
}

/* ------------------------------ cog_decide -------------------------------- */

function makeDecideTool() {
	return {
		name: "cog_decide",
		description:
			"Decide under uncertainty: options with probabilistic outcomes get expected value, risk (sigma), a risk-profile-adjusted score, regret, and dominance checks. Use when outcomes are probabilistic (in contrast to levh_decide, which handles certain weighted-criteria scoring and records to LEVH).",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["options"],
			properties: {
				options: {
					type: "array",
					description: "At least 2 options; each option's outcome probabilities must sum to 1.",
					items: {
						type: "object",
						additionalProperties: false,
						required: ["name", "outcomes"],
						properties: {
							name: { type: "string" },
							outcomes: {
								type: "array",
								items: {
									type: "object",
									additionalProperties: false,
									required: ["description", "probability", "value"],
									properties: {
										description: { type: "string" },
										probability: { type: "number", description: "In [0, 1]; probabilities of one option must sum to 1." },
										value: { type: "number", description: "Value/utlity if this outcome happens." },
									},
								},
							},
						},
					},
				},
				risk_profile: { type: "string", enum: ["neutral", "averse", "seeking"], description: "averse penalizes sigma by 0.5, seeking rewards it by 0.25, neutral ignores it (default neutral)." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["leader", "ranking", "dominates"],
				properties: {
					leader: { type: "string" },
					ranking: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["name", "ev", "sigma", "adjusted", "regret"],
							properties: {
								name: { type: "string" },
								ev: { type: "number" },
								sigma: { type: "number" },
								adjusted: { type: "number" },
								regret: { type: "number" },
							},
						},
					},
					dominates: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["a", "b"],
							properties: { a: { type: "string" }, b: { type: "string" } },
						},
					},
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						`cog_decide: leader "${value.leader}". ` +
						`Ranking: ${value.ranking.map((r) => `${r.name} EV ${r.ev} (sigma ${r.sigma}, adjusted ${r.adjusted}, regret ${r.regret})`).join(" | ")}.` +
						(value.dominates.length ? ` Dominance: ${value.dominates.map((d) => `${d.a} dominates ${d.b}`).join("; ")}.` : ""),
				},
			],
		},
		presentCall: () => ({ card: "generic", title: "cog_decide: belirsizlik altında karar", kind: "other" }),
		isConcurrencySafe: () => true,
		async execute(args) {
			const rawOptions = Array.isArray(args.options) ? args.options : null;
			if (!rawOptions || rawOptions.length < 2) throw new Error("invalid input: `options` needs at least 2 entries");
			const profile = args.risk_profile === undefined ? "neutral" : args.risk_profile;
			if (!["neutral", "averse", "seeking"].includes(profile)) throw new Error("invalid input: risk_profile must be neutral | averse | seeking");
			const seen = new Set();
			const options = rawOptions.map((option, index) => {
				if (!option || typeof option.name !== "string" || option.name.trim() === "") throw new Error(`invalid input: options[${index}] needs a non-empty name`);
				if (seen.has(option.name)) throw new Error(`invalid input: duplicate option ${option.name}`);
				if (!Array.isArray(option.outcomes) || option.outcomes.length === 0) throw new Error(`invalid input: option ${option.name} needs outcomes`);
				let probSum = 0;
				const outcomes = option.outcomes.map((outcome, j) => {
					if (!outcome || typeof outcome.description !== "string" || outcome.description.trim() === "") {
						throw new Error(`invalid input: ${option.name} outcome[${j}] needs a description`);
					}
					const probability = Number(outcome.probability);
					const value = Number(outcome.value);
					if (!Number.isFinite(probability) || probability < 0 || probability > 1) throw new Error(`invalid input: ${option.name} outcome "${outcome.description}" probability must be in [0, 1]`);
					if (!Number.isFinite(value)) throw new Error(`invalid input: ${option.name} outcome "${outcome.description}" value must be a finite number`);
					probSum += probability;
					return { description: outcome.description, probability, value };
				});
				if (probSum < 0.995 || probSum > 1.005) throw new Error(`invalid input: outcome probabilities of ${option.name} sum to ${round4(probSum)}, expected 1`);
				const ev = outcomes.reduce((sum, o) => sum + o.probability * o.value, 0);
				const variance = outcomes.reduce((sum, o) => sum + o.probability * (o.value - ev) * (o.value - ev), 0);
				const sigma = Math.sqrt(variance);
				const adjusted = profile === "averse" ? ev - 0.5 * sigma : profile === "seeking" ? ev + 0.25 * sigma : ev;
				seen.add(option.name);
				return { name: option.name, outcomes, ev, sigma, adjusted };
			});
			const maxEv = Math.max(...options.map((o) => o.ev));
			const ranking = options
				.map((o) => ({ name: o.name, ev: round4(o.ev), sigma: round4(o.sigma), adjusted: round4(o.adjusted), regret: round4(maxEv - o.ev) }))
				.sort((a, b) => b.adjusted - a.adjusted || a.name.localeCompare(b.name));
			const dominates = [];
			for (const a of options) {
				for (const b of options) {
					if (a.name === b.name) continue;
					const minA = Math.min(...a.outcomes.map((o) => o.value));
					const maxB = Math.max(...b.outcomes.map((o) => o.value));
					if (minA >= maxB) dominates.push({ a: a.name, b: b.name });
				}
			}
			return { leader: ranking[0].name, ranking, dominates };
		},
	};
}

/* ------------------------------ cog_reflect ------------------------------- */

function makeReflectTool(cfg) {
	return {
		name: "cog_reflect",
		description:
			"Close the learning loop after a decision or task: compares what was expected with what happened, computes the prediction error, flags overconfidence, composes a structured lesson, and can store it into LEVH memory (importance scales with the error). Use at the end of meaningful work.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["decision", "expected", "outcome", "lesson"],
			properties: {
				decision: { type: "string", description: "What was decided/done." },
				expected: { type: "string", description: "What was expected; a number if quantitative." },
				outcome: { type: "string", description: "What actually happened; a number if quantitative." },
				lesson: { type: "string", description: "The transferable lesson learned." },
				confidence: { type: "number", description: "Confidence before the outcome, in [0, 1]; enables the overconfidence flag." },
				tags: { type: "array", items: { type: "string" }, description: "Extra tags for the stored memory." },
				project: { type: "string", description: "LEVH project scope; empty uses the configured defaultProject." },
				store: { type: "boolean", description: "Store the lesson in LEVH (default true)." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["importance", "content", "levh"],
				properties: {
					prediction_error: { type: "number" },
					relative_error: { type: "number" },
					overconfidence: { type: "boolean" },
					importance: { type: "number" },
					content: { type: "string" },
					levh: { type: "object", additionalProperties: true, description: "Store outcome: stored, admission status, id, optional error." },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						`cog_reflect: lesson composed (importance ${value.importance}` +
						(value.prediction_error !== undefined ? `, prediction error ${value.prediction_error}` : "") +
						(value.overconfidence ? ", OVERCONFIDENCE flag" : "") +
						`). ` +
						(value.levh.stored ? `Stored in LEVH (admission: ${value.levh.admission ?? "n/a"}).` : value.levh.error ? `Not stored: ${value.levh.error}` : "Not stored."),
				},
			],
		},
		timeoutMs: cfg.timeoutMs + 2000,
		presentCall: (args) => ({ card: "generic", title: "cog_reflect: ders çıkarma", kind: "other", rawInput: String(args.decision).slice(0, 120) }),
		isConcurrencySafe: () => false,
		async execute(args) {
			for (const key of ["decision", "expected", "outcome", "lesson"]) {
				if (typeof args[key] !== "string" || args[key].trim() === "") throw new Error(`invalid input: \`${key}\` must be a non-empty string`);
			}
			const confidence = args.confidence === undefined ? null : Number(args.confidence);
			if (confidence !== null && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) throw new Error("invalid input: confidence must be in [0, 1]");
			const expectedNum = firstNumber(args.expected);
			const outcomeNum = firstNumber(args.outcome);
			const numeric = expectedNum !== null && outcomeNum !== null;
			const predictionError = numeric ? round4(outcomeNum - expectedNum) : undefined;
			const relativeError = numeric ? round4(Math.abs(predictionError) / (Math.abs(expectedNum) || 1)) : undefined;
			const overconfidence = numeric && confidence !== null && confidence >= 0.7 && relativeError > 0.25;
			const importance = numeric ? Math.min(0.95, round4(0.5 + relativeError)) : 0.6;
			const lines = [
				`**What**: ${args.decision}`,
				`**Expected**: ${args.expected}`,
				`**Outcome**: ${args.outcome}`,
				`**Learned**: ${args.lesson}`,
			];
			if (overconfidence) lines.push(`**Bias signal**: overconfidence (stated confidence ${confidence}, relative error ${relativeError}).`);
			const content = lines.join("\n");
			const project = args.project !== undefined ? String(args.project) : cfg.defaultProject;
			const levh = { stored: false };
			if (args.store !== false) {
				if (!project) {
					levh.error = "no project scope configured; pass `project` or set defaultProject";
				} else {
					try {
						const result = await postJson(cfg, "/api/v1/memories", {
							content,
							importance,
							tags: [...(Array.isArray(args.tags) ? args.tags.map(String) : []), "cog-reflection"],
							project,
						});
						levh.stored = true;
						levh.admission = result.status ?? result.verdict ?? null;
						levh.id = result.id ?? result.memory_id ?? null;
					} catch (error) {
						levh.error = String((error && error.message) || error);
					}
				}
			}
			const out = { importance, content, levh };
			if (predictionError !== undefined) out.prediction_error = predictionError;
			if (relativeError !== undefined) out.relative_error = relativeError;
			if (overconfidence) out.overconfidence = true;
			return out;
		},
	};
}

/* ------------------------------- cog_think -------------------------------- */

const THINK_WEIGHTS = { irreversibility: 0.3, stake: 0.3, ambiguity: 0.2, novelty: 0.2, time_pressure: -0.15 };
const THINK_SIGNALS = ["irreversibility", "stake", "ambiguity", "novelty", "time_pressure"];

function makeThinkTool() {
	return {
		name: "cog_think",
		description:
			"Route a problem through dual-process cognition: rate the signals (irreversibility, stake, ambiguity, novelty, time pressure) and the tool computes a deliberation demand, then mandates the protocol — System 1 act-now, a hybrid pass, or full System 2 deliberation with named tool steps. Use at the start of a non-trivial task to make the thinking mode an explicit, auditable choice.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["question"],
			properties: {
				question: { type: "string", description: "The problem or decision at hand." },
				signals: {
					type: "object",
					additionalProperties: true,
					description: "Map of signal name to rating in [0, 10]: irreversibility, stake, ambiguity, novelty, time_pressure. Missing signals default to 5.",
				},
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["route", "demand", "protocol", "signals_used"],
				properties: {
					route: { type: "string", description: "system2 | hybrid | system1" },
					demand: { type: "number" },
					protocol: { type: "array", items: { type: "string" } },
					signals_used: { type: "object", additionalProperties: true },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text: `cog_think: route ${value.route.toUpperCase()} (demand ${value.demand}/10). Protocol: ${value.protocol.join(" -> ")}`,
				},
			],
		},
		presentCall: (args) => ({ card: "generic", title: "cog_think: düşünme modu seçimi", kind: "other", rawInput: String(args.question).slice(0, 120) }),
		isConcurrencySafe: () => true,
		async execute(args) {
			if (typeof args.question !== "string" || args.question.trim() === "") throw new Error("invalid input: `question` must be a non-empty string");
			const rawSignals = args.signals && typeof args.signals === "object" && !Array.isArray(args.signals) ? args.signals : {};
			const signalsUsed = {};
			let demand = 0;
			for (const signal of THINK_SIGNALS) {
				const weight = THINK_WEIGHTS[signal];
				let value = 5;
				if (rawSignals[signal] !== undefined) {
					const n = Number(rawSignals[signal]);
					if (!Number.isFinite(n) || n < 0 || n > 10) throw new Error(`invalid input: signal "${signal}" must be a number in [0, 10]`);
					value = n;
				}
				signalsUsed[signal] = value;
				demand += weight * value;
			}
			demand = Math.max(0, Math.min(10, round4(demand)));
			if (demand >= 6) {
				return {
					route: "system2",
					demand,
					protocol: [
						"cog_deduce over the known facts and rules",
						"cog_deliberate with >=3 branches under different frames",
						"cog_decide (uncertainty) or levh_decide (certainty + record)",
						"levh_guard_check before any risky action",
						"cog_reflect after the outcome lands",
					],
					signals_used: signalsUsed,
				};
			}
			if (demand >= 3) {
				return {
					route: "hybrid",
					demand,
					protocol: ["one cog_deliberate pass (>=3 branches)", "levh_guard_check before acting", "cog_reflect if the outcome surprises you"],
					signals_used: signalsUsed,
				};
			}
			return {
				route: "system1",
				demand,
				protocol: ["act directly", "cog_reflect if the outcome surprises you"],
				signals_used: signalsUsed,
			};
		},
	};
}

/* ----------------------------- cog_bias_check ----------------------------- */

const SUNK_COST_STEMS = ["zaten", "yatirim", "investment", "already", "spent", "harcad", "gommen", "embedded"];
const URGENCY_STEMS = ["acil", "urgent", "hemen", "deadline", "songun", "asap"];

function hasStem(wordSet, stems) {
	for (const stem of stems) {
		for (const word of wordSet) if (word.startsWith(stem)) return true;
	}
	return false;
}

function makeBiasCheckTool() {
	return {
		name: "cog_bias_check",
		description:
			"Scan a formed decision for known cognitive biases with deterministic structural and keyword rules: anchoring (too few options), sunk-cost language, confirmation starvation, overconfidence, uniform score clusters, urgency pressure. Returns each flag with a concrete debiasing action. Use right before committing.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["decision", "rationale", "options_considered", "evidence_count"],
			properties: {
				decision: { type: "string", description: "The decision about to be committed." },
				rationale: { type: "string", description: "The stated reasoning; scanned for sunk-cost and urgency language." },
				options_considered: { type: "integer", description: "How many real alternatives were generated (not variants)." },
				evidence_count: { type: "integer", description: "How of the observations actively checked against the choice." },
				confidence: { type: "number", description: "Stated confidence in [0, 1]." },
				score_variance: { type: "number", description: "Variance of the option scores, if scored; low variance with several options flags uniformity." },
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["flags", "clean"],
				properties: {
					flags: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["bias", "severity", "debias"],
							properties: { bias: { type: "string" }, severity: { type: "string", description: "high | medium | low" }, debias: { type: "string" } },
						},
					},
					clean: { type: "boolean" },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text: value.clean
						? "cog_bias_check: no structural bias flags — commit with a clean sheet."
						: `cog_bias_check: ${value.flags.length} flag(s). ${value.flags.map((f) => `[${f.severity}] ${f.bias}: ${f.debias}`).join(" | ")}`,
				},
			],
		},
		presentCall: (args) => ({ card: "generic", title: "cog_bias_check: önyargı taraması", kind: "other", rawInput: String(args.decision).slice(0, 120) }),
		isConcurrencySafe: () => true,
		async execute(args) {
			for (const key of ["decision", "rationale"]) {
				if (typeof args[key] !== "string" || args[key].trim() === "") throw new Error(`invalid input: \`${key}\` must be a non-empty string`);
			}
			const options = Number(args.options_considered);
			const evidence = Number(args.evidence_count);
			if (!Number.isInteger(options) || options < 1) throw new Error("invalid input: options_considered must be an integer >= 1");
			if (!Number.isInteger(evidence) || evidence < 0) throw new Error("invalid input: evidence_count must be an integer >= 0");
			const confidence = args.confidence === undefined ? null : Number(args.confidence);
			if (confidence !== null && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) throw new Error("invalid input: confidence must be in [0, 1]");
			const wordSet = new Set(words(args.rationale));
			const flags = [];
			if (options < 2) flags.push({ bias: "anchoring", severity: "high", debias: "generate at least 2 genuinely different alternatives before committing" });
			if (hasStem(wordSet, SUNK_COST_STEMS)) flags.push({ bias: "sunk-cost", severity: "medium", debias: "judge the option on future value only; past spend is gone either way" });
			if (evidence === 0) flags.push({ bias: "confirmation-starved", severity: "high", debias: "actively seek at least one observation that could disconfirm the choice" });
			if (confidence !== null && confidence >= 0.8 && evidence <= 1) flags.push({ bias: "overconfidence", severity: "high", debias: "high confidence on thin evidence — lower it or gather more observations" });
			const scoreVariance = args.score_variance === undefined ? null : Number(args.score_variance);
			if (scoreVariance !== null) {
				if (!Number.isFinite(scoreVariance) || scoreVariance < 0) throw new Error("invalid input: score_variance must be a finite number >= 0");
				if (options >= 2 && scoreVariance < 0.4) flags.push({ bias: "uniformity", severity: "medium", debias: "option scores are clustered — re-score each option independently before comparing" });
			}
			if (hasStem(wordSet, URGENCY_STEMS) && options < 3) flags.push({ bias: "urgency", severity: "medium", debias: "time pressure plus few options — buy a short pause and widen the option set" });
			return { flags, clean: flags.length === 0 };
		},
	};
}

/* ------------------------------ cog_analogize ----------------------------- */

function makeAnalogizeTool() {
	return {
		name: "cog_analogize",
		description:
			"Run structure mapping between a known source situation and a target situation: relations with matching predicates are mapped, unmapped target relations are gaps, and unmapped source relations become explicit transfer hypotheses. Use when a familiar situation resembles the current one and you want the mapping — and its risks — spelled out.",
		parameters: {
			type: "object",
			additionalProperties: false,
			required: ["source", "target"],
			properties: {
				source: {
					type: "object",
					additionalProperties: false,
					required: ["domain", "relations"],
					properties: {
						domain: { type: "string", description: "The familiar situation." },
						relations: {
							type: "array",
							items: {
								type: "object",
								additionalProperties: false,
								required: ["from", "predicate", "to"],
								properties: { from: { type: "string" }, predicate: { type: "string" }, to: { type: "string" } },
							},
						},
					},
				},
				target: {
					type: "object",
					additionalProperties: false,
					required: ["domain", "relations"],
					properties: {
						domain: { type: "string", description: "The current situation." },
						relations: {
							type: "array",
							items: {
								type: "object",
								additionalProperties: false,
								required: ["from", "predicate", "to"],
								properties: { from: { type: "string" }, predicate: { type: "string" }, to: { type: "string" } },
							},
						},
					},
				},
			},
		},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				required: ["mapped", "gaps", "transfers", "alignment"],
				properties: {
					mapped: {
						type: "array",
						items: {
							type: "object",
							additionalProperties: false,
							required: ["predicate", "source", "target"],
							properties: {
								predicate: { type: "string" },
								source: { type: "string" },
								target: { type: "string" },
							},
						},
					},
					gaps: { type: "array", items: { type: "string" } },
					transfers: { type: "array", items: { type: "string" } },
					alignment: { type: "number", description: "Share of target relations mapped to the source, in [0, 1]." },
				},
			},
			render: (_args, value) => [
				{
					type: "text",
					text:
						`cog_analogize: alignment ${value.alignment}. ` +
						`Mapped: ${value.mapped.map((m) => `${m.predicate} (${m.source} ~ ${m.target})`).join("; ") || "none"}. ` +
						(value.gaps.length ? `Gaps: ${value.gaps.join("; ")}. ` : "") +
						(value.transfers.length ? `Transfer hypotheses: ${value.transfers.join("; ")}` : ""),
				},
			],
		},
		presentCall: () => ({ card: "generic", title: "cog_analogize: yapı eşleme", kind: "other" }),
		isConcurrencySafe: () => true,
		async execute(args) {
			for (const side of ["source", "target"]) {
				const part = args[side];
				if (!part || typeof part.domain !== "string" || part.domain.trim() === "" || !Array.isArray(part.relations) || part.relations.length === 0) {
					throw new Error(`invalid input: \`${side}\` needs a domain and a non-empty relations array`);
				}
				part.relations.forEach((relation, index) => {
					if (!relation || typeof relation.from !== "string" || typeof relation.predicate !== "string" || relation.predicate.trim() === "" || typeof relation.to !== "string") {
						throw new Error(`invalid input: ${side} relation[${index}] needs from/predicate/to strings`);
					}
				});
			}
			const mapped = [];
			const gaps = [];
			for (const targetRelation of args.target.relations) {
				const match = args.source.relations.find((r) => r.predicate === targetRelation.predicate);
				if (match) {
					mapped.push({
						predicate: targetRelation.predicate,
						source: `${match.from} ${match.predicate} ${match.to}`,
						target: `${targetRelation.from} ${targetRelation.predicate} ${targetRelation.to}`,
					});
				} else {
					gaps.push(`${targetRelation.from} ${targetRelation.predicate} ${targetRelation.to} (no source analogue)`);
				}
			}
			const transfers = [];
			for (const sourceRelation of args.source.relations) {
				if (!args.target.relations.some((r) => r.predicate === sourceRelation.predicate)) {
					transfers.push(`${args.target.domain} may also: ${sourceRelation.from} ${sourceRelation.predicate} ${sourceRelation.to} — verify before relying on it`);
				}
			}
			return { mapped, gaps, transfers, alignment: round4(mapped.length / args.target.relations.length) };
		},
	};
}

/** Register the cognitive tools; the disposer unregisters them all. */
export function apply(ctx, rawConfig) {
	const cfg = readConfig(rawConfig);
	const definitions = [
		makeThinkTool(),
		makeDeduceTool(),
		makeHypothesizeTool(),
		makeDeliberateTool(),
		makeDecideTool(),
		makeBiasCheckTool(),
		makeAnalogizeTool(),
		makeReflectTool(cfg),
	];
	return ctx.effect(() => {
		const disposers = definitions.map((definition) => ctx.tools.register(definition));
		return () => {
			for (const dispose of disposers) dispose();
		};
	});
}

export { inject, name };
