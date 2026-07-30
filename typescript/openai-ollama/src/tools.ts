export const TOOL_SCHEMAS = [
  {
    type: 'function' as const,
    function: {
      name: 'get_weather',
      description: 'Get the current weather for a city',
      parameters: {
        type: 'object',
        properties: { city: { type: 'string', description: 'City name' } },
        required: ['city'],
      },
    },
  },
];

export function dispatch(name: string, args: Record<string, unknown>) {
  if (name !== 'get_weather') throw new Error(`unknown tool: ${name}`);
  return { city: args.city ?? 'unknown', temperature_c: 34, conditions: 'clear', humidity_pct: 55 };
}
