import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assetIdFor, collectVariableAliasIds, detectImageFormat, isIconCandidate, suggestRole, validateRoles, vectorGroupExport, maskGroupExport, RASTER_THRESHOLD, pageName, stateSiblings, stateOfValue, triggerKind, reactionActions } from '../src/util.ts';

test('suggestRole by frame width', () => {
  assert.equal(suggestRole(1440), 'desktop');
  assert.equal(suggestRole(1024), 'desktop');
  assert.equal(suggestRole(768), 'tablet');
  assert.equal(suggestRole(390), 'mobile');
});

test('assetIdFor makes file-safe ids', () => {
  assert.equal(assetIdFor('svg', 'I12:34;56:78'), 'svg-I12_34_56_78');
  assert.match(assetIdFor('img', 'abc/def'), /^[A-Za-z0-9_.-]+$/);
});

test('detectImageFormat from magic bytes', () => {
  assert.equal(detectImageFormat(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0, 0])), 'PNG');
  assert.equal(detectImageFormat(new Uint8Array([0xff, 0xd8, 0xff, 0xe0])), 'JPG');
  assert.equal(detectImageFormat(new Uint8Array([0x47, 0x49, 0x46, 0x38])), 'GIF');
  const webp = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]);
  assert.equal(detectImageFormat(webp), 'WEBP');
  assert.equal(detectImageFormat(new Uint8Array([1, 2, 3])), null);
});

test('collectVariableAliasIds finds nested aliases', () => {
  const ids = collectVariableAliasIds({
    a: { boundVariables: { fills: [{ type: 'VARIABLE_ALIAS', id: 'VariableID:1:2' }] } },
    b: [{ x: { type: 'VARIABLE_ALIAS', id: 'VariableID:3:4' } }],
  });
  assert.deepEqual([...ids].sort(), ['VariableID:1:2', 'VariableID:3:4']);
});

test('isIconCandidate', () => {
  const base = { type: 'FRAME', width: 24, height: 24, hasImageFill: false, descendantTypes: ['VECTOR', 'RECTANGLE'] };
  assert.equal(isIconCandidate(base), true);
  assert.equal(isIconCandidate({ ...base, width: 300 }), false, 'too large');
  assert.equal(isIconCandidate({ ...base, descendantTypes: ['VECTOR', 'TEXT'] }), false, 'has text');
  assert.equal(isIconCandidate({ ...base, descendantTypes: ['RECTANGLE'] }), false, 'no real vector');
  assert.equal(isIconCandidate({ ...base, hasImageFill: true }), false, 'image fill');
  assert.equal(isIconCandidate({ ...base, type: 'TEXT' }), false, 'not a container');
});

test('validateRoles allows several pages', () => {
  assert.equal(validateRoles({}), 'Select at least one frame.');
  assert.equal(validateRoles({ a: 'mobile' }), null, 'a phone-only design: the mobile frame is the page');
  assert.equal(validateRoles({ a: 'desktop', b: 'desktop' }), null, 'two pages');
  assert.equal(validateRoles({ a: 'desktop', b: 'mobile' }), null);
});

test('pageName strips breakpoints and devices', () => {
  assert.equal(pageName('Home / Desktop'), 'Home');
  assert.equal(pageName('Home - Mobile'), 'Home');
  assert.equal(pageName('About Us (1440)'), 'About Us');
  assert.equal(pageName('Contact – iPhone 14 Pro'), 'Contact');
  assert.equal(pageName('Peachy Pup Bakery'), 'Peachy Pup Bakery');
});

test('vectorGroupExport collapses illustrations of any size', () => {
  assert.equal(vectorGroupExport('GROUP', ['VECTOR', 'VECTOR', 'GROUP', 'ELLIPSE'], false), 'svg');
  assert.equal(vectorGroupExport('FRAME', ['VECTOR', 'TEXT'], false), null, 'text is real content');
  assert.equal(vectorGroupExport('GROUP', ['RECTANGLE', 'RECTANGLE'], false), null, 'no true vector: keep as shapes');
  assert.equal(vectorGroupExport('GROUP', ['VECTOR'], true), null, 'image fill');
  assert.equal(vectorGroupExport('TEXT', ['VECTOR'], false), null, 'not a container');
  const many = Array(RASTER_THRESHOLD + 1).fill('VECTOR');
  assert.equal(vectorGroupExport('GROUP', many, false), 'png', 'very complex illustration → PNG');
});

test('maskGroupExport flattens mask groups unless they hold text', () => {
  assert.equal(maskGroupExport('GROUP', true, false), 'png');
  assert.equal(maskGroupExport('FRAME', true, false), 'png');
  assert.equal(maskGroupExport('GROUP', true, true), null); // text stays live; the Engine masks with CSS
  assert.equal(maskGroupExport('GROUP', false, false), null);
  assert.equal(maskGroupExport('TEXT', true, false), null);
});

test('state variants next to the default one are found, wired or not', () => {
  const base = { State: 'Default', Size: 'Large' };
  const siblings = [
    { State: 'Default', Size: 'Small' },
    { State: 'Hover', Size: 'Large' },
    { State: 'Pressed', Size: 'Large' },
    { State: 'Hover', Size: 'Small' },
    { State: 'Focused', Size: 'Large' },
    { State: 'Disabled', Size: 'Large' },
  ];
  assert.deepEqual(stateSiblings(base, siblings), [[1, 'hover'], [2, 'press'], [4, 'focus']]);
  assert.deepEqual(stateSiblings({ State: 'Hover' }, [{ State: 'Default' }]), []); // a state is not a base
  assert.equal(stateOfValue('on hover'), 'hover');
  assert.equal(stateOfValue('Primary'), null);
});

test('prototype triggers map to web events', () => {
  assert.equal(triggerKind({ type: 'ON_HOVER' }), 'hover');
  assert.equal(triggerKind({ type: 'ON_PRESS' }), 'press');
  assert.equal(triggerKind({ type: 'ON_CLICK' }), 'click');
  assert.equal(triggerKind({ type: 'AFTER_TIMEOUT' }), 'timeout');
  assert.equal(triggerKind({ type: 'MOUSE_ENTER' }), 'enter');
  assert.equal(triggerKind(null), 'other');
  assert.deepEqual(reactionActions({ action: { type: 'BACK' } }), [{ type: 'BACK' }]);
  assert.deepEqual(reactionActions({ actions: [{ type: 'URL', url: 'x' }] }), [{ type: 'URL', url: 'x' }]);
});
