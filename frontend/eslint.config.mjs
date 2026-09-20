import nextVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = [
  ...nextVitals,
  {
    rules: {
      // Existing codebase uses the "sync props/server state into local state"
      // effect idiom in ~23 places. Adopting the react-hooks v6 preferred
      // pattern (derive during render / key-based reset) is a dedicated
      // refactor, not part of the eslint 9 toolchain upgrade.
      "react-hooks/set-state-in-effect": "off",
    },
  },
];

export default eslintConfig;
