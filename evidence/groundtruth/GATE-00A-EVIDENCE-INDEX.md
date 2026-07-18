# Gate 0A Evidence Index

TESTED_RUNTIME_SHA: `3a97ae7177c128e5484434d76828751330149fc3`
PR_BASE_SHA: `034113e91feb442d480e9071612c50ce6092d486`
Branch: `audit/groundtruth-v2`

Every SHA-256 below was computed after review correction, harness separation,
evidence sanitization, pytest normalization and cache cleanup. This index
excludes its own checksum to avoid a circular digest.

| Relative path | Bytes | SHA-256 |
|---|---:|---|
| `docs/groundtruth/00A1-cross-process-coherence.md` | 6897 | `7ee20e9cacd5ed4a3cba522972638f3079488eb07fa5be730e2cb509fd2ff78b` |
| `docs/groundtruth/00A2-explicit-network-consent.md` | 6613 | `8711a989d79af5f48c98894b2fe16d5a1150a18a5bef3d2c91bc727b9d7d246e` |
| `docs/groundtruth/00A3-content-update-admission-invariant.md` | 8368 | `aa1bea8d11d8e4f51c20c3b05fb7bd3a59fe4a0d491df7f99d83db88cbcf9e7f` |
| `docs/groundtruth/00A4-standalone-sse-auth-boundary.md` | 7145 | `f40308a8bc60f0c47f21d2d94dddb6fe84300414d6e0295eca17c46fe4bcb29e` |
| `docs/groundtruth/00A-merge-manifest.md` | 3229 | `dc409517d55ba9fa692706e8265c059afddf5ee57f39fc3376298a81e35fa1f5` |
| `docs/groundtruth/00A-p0-runtime-findings.md` | 3688 | `814e8905b59d93ba81ddc1fec090d6b3d1f6fe406122863b6d08a65374f01de3` |
| `docs/groundtruth/00B-runtime-invariants.md` | 7011 | `91610d5167d5344db662e704ce7c24fe58654840024547d37c61f289ee5dcf7b` |
| `evidence/groundtruth/GATE-00A-MERGE-PREP-REPORT.md` | 3572 | `33ac2b08bcbab1e717daada58d6b46a87861080039ce88d4e8e29f3fc1a9f284` |
| `evidence/groundtruth/task-00A1/commands.txt` | 2954 | `d2d6190815758d6685f3cdcc42ccafdb45e5f9e2838657f6d47b37aa461b3b08` |
| `evidence/groundtruth/task-00A1/engine-scenarios.jsonl` | 12943 | `41072feaf813de7e88f21fd20d6a5db0b65000499d1f4d3a840e6823b444459a` |
| `evidence/groundtruth/task-00A1/harness/reproduce_cross_process_coherence.py` | 40211 | `05411ff34a5e02070f2305b32a7c12d5f649676d3458e6bad55f1fdb9d004f77` |
| `evidence/groundtruth/task-00A1/process-map.txt` | 1012 | `cb04c14cfe9f62621a3843a86c203f40d8346b6050c64f13d955d0f45afcaadb` |
| `evidence/groundtruth/task-00A1/sqlite-state.txt` | 1170 | `66c6648a43b6f0cb6d3bbe0d5f78879e88ac7fb9077e960ed1475f2f3f67fc04` |
| `evidence/groundtruth/task-00A1/stderr/engine-A.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A1/stderr/engine-A-restarted.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A1/stderr/engine-B.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A1/stderr/engine-B-restarted.log` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A1/stderr/mcp-initial.log` | 929 | `2f5854ed842bcccf60bc4bd3de620ee05d2901c4331053a651ad47f049db9326` |
| `evidence/groundtruth/task-00A1/stderr/mcp-restarted.log` | 554 | `2a1c7a8694c8d8aee71f1ea7484a561da4e5d2a741c1a39e2ef3a96b6124e646` |
| `evidence/groundtruth/task-00A1/stderr/rest-initial.log` | 200 | `b03dd8e46f78d28bd37eaa35b34e33172f79a4cf5942c13c52355f74df3fed2d` |
| `evidence/groundtruth/task-00A1/stderr/rest-restarted.log` | 199 | `e12e7d3c69622b74f37a1772cb68aab6351b36c7e6f8f349d8b362e4a5ba309a` |
| `evidence/groundtruth/task-00A1/stdout/engine-A.jsonl` | 4685 | `550d1b32ada1690214442909d30ab3360f9fbeebe61adce62d3760da20170b2f` |
| `evidence/groundtruth/task-00A1/stdout/engine-A-restarted.jsonl` | 1704 | `b8941348bd2f89b4bf033af5c9b45d89c7e57971b4fd62257b6dff0d8312e3f1` |
| `evidence/groundtruth/task-00A1/stdout/engine-B.jsonl` | 4682 | `c8d77ef6537bee2812066ad8f458ea08bfca1491afcd196da594f4c768b76890` |
| `evidence/groundtruth/task-00A1/stdout/engine-B-restarted.jsonl` | 1703 | `9b83f38b19c14c507d85ad242ba7cb2af497613005ef7cbfe31d88afccea5752` |
| `evidence/groundtruth/task-00A1/stdout/mcp-tool-results.jsonl` | 8238 | `a5725a134f354dcbb2beb9a3bfa6ff2492aaff4dc03a7011fbacac9909a0d009` |
| `evidence/groundtruth/task-00A1/stdout/rest-initial.log` | 858 | `46b65359ea282467eac6f30304eddbde07725c6cf598c26e1957f7f950bfd7e2` |
| `evidence/groundtruth/task-00A1/stdout/rest-restarted.log` | 292 | `59289d861d6aa2da3dd2cc152a7ad77f9cf4c29626472552c4748a9b91e9e18d` |
| `evidence/groundtruth/task-00A1/transport-scenarios.jsonl` | 35525 | `b122418861b916f197cd2bf787271ff53ca19dc7fff05decf8564a3bcac94b11` |
| `evidence/groundtruth/task-00A2/captured-httpx-posts.jsonl` | 2370 | `179ec098c1e77b9d50ecd61b50b347b85f4be85f10bd7bb1c3fd65eb6dbd5e03` |
| `evidence/groundtruth/task-00A2/commands.txt` | 2426 | `91a7865e616fcdbed5f207d20828718c71eaae9f42611a53f8e23b3030584ce4` |
| `evidence/groundtruth/task-00A2/harness/reproduce_explicit_network_consent.py` | 17650 | `c158002fa0a6e5c8a3801b070f24fd66bc318f125bb10625441180aee2a9a11e` |
| `evidence/groundtruth/task-00A2/network-guard.txt` | 174 | `ec7cafe6f449907d472c573c8a7f9e18b1c3777a4631ee52f29d0ce807e30312` |
| `evidence/groundtruth/task-00A2/scenarios.jsonl` | 3736 | `13b4ced7276db31b20b5fdfd08559b4993e2dfa8bd272fb92e3c59fa7f855217` |
| `evidence/groundtruth/task-00A2/stderr/pytest.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A2/stdout/pytest.txt` | 99 | `43bffcca7a79ac4899c70e7a223b2d6447938c051a27b0df9474f8a8c77e5848` |
| `evidence/groundtruth/task-00A3/commands.txt` | 3461 | `38552f8e54bcfc2061823b136fb8f5aa04b6496996cb16f6bd966c8d4bca945a` |
| `evidence/groundtruth/task-00A3/embedding-inputs.jsonl` | 2490 | `d7c9133e4e5ed539e1334f03ed3802132caedf03ec010884ade99631870d51be` |
| `evidence/groundtruth/task-00A3/harness/reproduce_update_admission_invariant.py` | 28430 | `3ca3eadb136181b5f18e98df55709662a9dbb9ed761c533c4e03c1db362f849e` |
| `evidence/groundtruth/task-00A3/persistence-state.jsonl` | 976 | `7d4133114f9130a1de42b23c008eb9946639e4f1774fae76bcba6ff24a852dfa` |
| `evidence/groundtruth/task-00A3/scenarios.jsonl` | 22592 | `d51ee916ae7bda2bf67a406654f8e4dd28a0f34b04fdb6580c3eace7c13b0eba` |
| `evidence/groundtruth/task-00A3/stderr/mcp.log` | 304 | `7033527b653bbb1aa7562d5502bdf21bd1050cf1dc094dac6e3536f31037ea60` |
| `evidence/groundtruth/task-00A3/stderr/pytest.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A3/stdout/mcp-tool-results.jsonl` | 266 | `e7b49127f5e52b7ad4dffba8e70b8f9abd9d01225b3665ea6471367a3a614afd` |
| `evidence/groundtruth/task-00A3/stdout/pytest.txt` | 98 | `eca5cbd94e503a23336828acfa5e001758d391b0f5bb7835ce3a9b67e494982e` |
| `evidence/groundtruth/task-00A3/surface-inventory.json` | 227 | `654e209566a6b7a07a2e5caec8d0d3d77359e45fb3f9e2e830a4c92f8f443d53` |
| `evidence/groundtruth/task-00A4/commands.txt` | 2732 | `ba8aca24ef7d5fa02da721691b2592f6169c930fb5e45f48311f3093c6025de6` |
| `evidence/groundtruth/task-00A4/harness/reproduce_standalone_sse_auth_boundary.py` | 19017 | `78207b548e2e4f26f76286d170b70cfec8c15b4c894bf97bc1e0d8268e0f6298` |
| `evidence/groundtruth/task-00A4/process-map.txt` | 1238 | `f2f4c053537712b7011342b4022dddc97f1606e493619d76073ca4b087d348cb` |
| `evidence/groundtruth/task-00A4/scenarios.jsonl` | 10360 | `3bb775ceb4d2234b9be58e0739947859801ea98ce804a99c44d12113457e9cd9` |
| `evidence/groundtruth/task-00A4/sqlite-state.txt` | 1197 | `b0235e98cf12ca7e3525bebcf858bded27756106eb6da22ecb7d97bca6a54d0e` |
| `evidence/groundtruth/task-00A4/stderr/main-fastapi-mounted-token-set.log` | 651 | `98f475e73bf42a73716e4988de7792c8d7eca751b02d50d73a0beae8c7160a22` |
| `evidence/groundtruth/task-00A4/stderr/pytest.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A4/stderr/standalone-default-no-token.log` | 1151 | `a318c9b195fb35cac36dba801c4afbadef1d70835ce511cc35b963f2ad2448cb` |
| `evidence/groundtruth/task-00A4/stderr/standalone-profile-full.log` | 652 | `79adfff3ec6cf4a7318300a508448612ba58212feec24ac4ce2d6290b5c0c9d9` |
| `evidence/groundtruth/task-00A4/stderr/standalone-profile-minimal.log` | 652 | `ceed30d0e72c56595f4c03244193f46837d5c47fa52302600c3367db091a14c7` |
| `evidence/groundtruth/task-00A4/stderr/standalone-profile-work.log` | 652 | `85f7af49bd299ba40db93d832b452e82757b7192edf3554d3b84b1909b5bb0e4` |
| `evidence/groundtruth/task-00A4/stderr/standalone-token-set-client-omits-token.log` | 1151 | `331fb989fe4728f4030ba8c7bd87ba85b1a343b68b637e34546c7dcdf26c0a2e` |
| `evidence/groundtruth/task-00A4/stdout/main-fastapi-mounted-token-set.log` | 1332 | `b458bfd28d385ca5cbd5b2b4a79fdefc763ba312e17367d609b1e912ff90318a` |
| `evidence/groundtruth/task-00A4/stdout/pytest.txt` | 99 | `a630ee67aa0e709411db6bf103cdca97a87f0e17318f63d68ad1a099b65495de` |
| `evidence/groundtruth/task-00A4/stdout/standalone-default-no-token.log` | 1362 | `e73339ae06a41946a278d3fd7f48b40315fb241be741e32bf3a12527809cb4cb` |
| `evidence/groundtruth/task-00A4/stdout/standalone-profile-full.log` | 910 | `5af93844ef33af6bd93dabe32240eb589742939d3192471b9d67ba696581ebc9` |
| `evidence/groundtruth/task-00A4/stdout/standalone-profile-minimal.log` | 910 | `1a20bc16ee1e5e57b1cdc2d72457851b71e52f172b24f8eba31a70b6212efe03` |
| `evidence/groundtruth/task-00A4/stdout/standalone-profile-work.log` | 910 | `831706aa7abc4d3bc359e9be0745a5adcc9f4b1fb9240afd27b7ee3153f13ecc` |
| `evidence/groundtruth/task-00A4/stdout/standalone-token-set-client-omits-token.log` | 1362 | `23be203885d144e2236d1945b671fc4044e5654470d601258a0297942435c24c` |
| `evidence/groundtruth/task-00A4/stdout/tool-results.jsonl` | 1170 | `01950494a7bf92f367b712615c3b39b695c9e58bc265418b74599fdfbf477ce9` |
| `evidence/groundtruth/task-00A4/tool-surfaces.jsonl` | 5054 | `4d21d6e191e930bb1a99358321549ebe81a8749c461fc811daee3c12b3dbdeec` |
| `evidence/groundtruth/task-00A5/commands.txt` | 1104 | `ca84bd68c1c81f538f7825dbe5c5fdeefe54c7c6ebf9ab8d793302977532db93` |
| `evidence/groundtruth/task-00A5/pytest-backend.txt` | 1148 | `66029bb8c5e2be486e26a45d968a1eaaeb30756c1f8b1f38b29463ac2de9fc6a` |
| `evidence/groundtruth/task-00A5/pytest-backend-stderr.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A5/pytest-collect.txt` | 35806 | `53f165179236f11617ebfa407cb815fca9d9cd1b8218269b873d5a43bff99f8b` |
| `evidence/groundtruth/task-00A5/pytest-collect-stderr.txt` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `evidence/groundtruth/task-00A5/sanitization-report.txt` | 737 | `e6e15f517e05c1224e3cfee646627a6486ef88b4e4e942b1df4754fda830ade6` |
| `evidence/groundtruth/task-00A6/iterations.jsonl` | 834 | `1f5c05b0d6751c6c54d354d1e67d00a78e08e5b51b3f913bd65bd4417e69e383` |
| `evidence/groundtruth/task-00A6/REVIEW-CLOSEOUT.md` | 2366 | `3c57fd236858bed6faff19d2a495dd3d863ac1dab1c4f365a4b6c7e1598604b1` |
| `tests/groundtruth/README.md` | 1869 | `326723fce0724aee425848340d86f518538bd713adaeec7292f52a0e53106750` |
| `tests/groundtruth/test_cross_process_coherence.py` | 3454 | `5be474a4271d49cc8e0bd2f8dae8c529eedba407c7f31fdcdf7be8ed98760027` |
| `tests/groundtruth/test_explicit_network_consent.py` | 2092 | `59e2bd23a7463b0046c08224395ee5835d179cdc32ad852a8e234335442e29c1` |
| `tests/groundtruth/test_standalone_sse_auth_boundary.py` | 2653 | `bdda4b9d1f2a03c60689912c9b9144f2add81ed8b87b96234743d8b1eb5ab69b` |
| `tests/groundtruth/test_update_admission_invariant.py` | 1879 | `41730c91021501ec53c42fa7d0d3593f1dff8209be2f73c1613a38b97ddb33a5` |

Indexed files: 80
