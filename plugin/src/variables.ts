// Collects local variables plus any library variables the selection is bound to.
import type { BundleNode, Diagnostic } from './types.ts';
import { collectVariableAliasIds } from './util.ts';

function plainVariable(v: Variable): Record<string, unknown> {
  return JSON.parse(JSON.stringify({
    id: v.id,
    name: v.name,
    key: v.key,
    resolvedType: v.resolvedType,
    collectionId: v.variableCollectionId,
    valuesByMode: v.valuesByMode,
    scopes: v.scopes,
    codeSyntax: v.codeSyntax,
    description: v.description,
    remote: v.remote,
  }));
}

function plainCollection(c: VariableCollection): Record<string, unknown> {
  return JSON.parse(JSON.stringify({
    id: c.id, name: c.name, key: c.key, modes: c.modes, defaultModeId: c.defaultModeId, remote: c.remote,
  }));
}

export async function collectVariables(nodes: Record<string, BundleNode>, diagnostics: Diagnostic[]) {
  const variables = new Map<string, Record<string, unknown>>();
  const collections = new Map<string, Record<string, unknown>>();

  try {
    for (const v of await figma.variables.getLocalVariablesAsync()) variables.set(v.id, plainVariable(v));
    for (const c of await figma.variables.getLocalVariableCollectionsAsync()) collections.set(c.id, plainCollection(c));
  } catch (e) {
    diagnostics.push({ nodeId: null, code: 'VARIABLES_FAILED', severity: 'warning', message: String(e) });
  }

  // Library (remote) variables used by the selection, and aliases between variables.
  const pending = collectVariableAliasIds(nodes);
  for (const v of variables.values()) collectVariableAliasIds(v.valuesByMode, pending);
  const seen = new Set<string>();
  while (pending.size > 0) {
    const id = pending.values().next().value as string;
    pending.delete(id);
    if (seen.has(id)) continue;
    seen.add(id);
    if (!variables.has(id)) {
      try {
        const v = await figma.variables.getVariableByIdAsync(id);
        if (v) {
          const plain = plainVariable(v);
          variables.set(id, plain);
          collectVariableAliasIds(plain.valuesByMode, pending);
        } else {
          diagnostics.push({ nodeId: null, code: 'VARIABLE_NOT_FOUND', severity: 'warning', message: `Variable ${id} is bound but not available.` });
        }
      } catch (e) {
        diagnostics.push({ nodeId: null, code: 'VARIABLE_FAILED', severity: 'warning', message: String(e) });
      }
    }
  }

  for (const v of variables.values()) {
    const cid = v.collectionId as string;
    if (cid && !collections.has(cid)) {
      try {
        const c = await figma.variables.getVariableCollectionByIdAsync(cid);
        if (c) collections.set(cid, plainCollection(c));
      } catch {
        /* collection unavailable; the variable still carries its values */
      }
    }
  }

  return { variables: [...variables.values()], variableCollections: [...collections.values()] };
}
