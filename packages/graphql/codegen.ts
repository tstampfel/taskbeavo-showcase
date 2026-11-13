import type { CodegenConfig } from '@graphql-codegen/cli';

/** Typed documents for web and mobile, generated from the exported backend schema. */
const config: CodegenConfig = {
  schema: './schema.graphql',
  documents: ['src/operations/**/*.graphql'],
  ignoreNoDocuments: false,
  generates: {
    './src/generated/': {
      preset: 'client',
      presetConfig: { fragmentMasking: false },
      config: {
        useTypeImports: true,
        enumsAsTypes: true,
        // Unknown custom scalars stay `unknown` until each gets an explicit mapping.
        defaultScalarType: 'unknown',
        strictScalars: false,
      },
    },
  },
};

export default config;
