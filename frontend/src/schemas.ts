import { z } from "zod";

export const ActiveUserSchema = z.object({
  id: z.string().optional().default(""),
  email: z.string().optional().default(""),
  fullname: z.string().optional().default("Penpot User"),
});

export const ValkeyStatsSchema = z.object({
  enabled: z.boolean().default(false),
  connected: z.boolean().default(false),
  hits: z.number().default(0),
  misses: z.number().default(0),
  errors: z.number().default(0),
  default_ttl: z.number().default(120),
});

export const PgvectorStatusSchema = z.object({
  connected: z.boolean().default(false),
  pgvector_installed: z.boolean().default(false),
  pgvector_version: z.string().optional().default(""),
  pg_trgm_version: z.string().optional().default(""),
  indexed_shapes_count: z.number().default(0),
  users_with_ai_profiles: z.number().default(0),
});

export const AgentStatusSchema = z.object({
  status: z.string(),
  active_user: ActiveUserSchema.optional(),
  active_provider: z.string().default("gemini"),
  default_model: z.string().default("gemini-3.5-flash-lite"),
  valkey_cache: ValkeyStatsSchema.optional(),
  pgvector: PgvectorStatusSchema.optional(),
  plugin: z
    .object({
      connected: z.boolean().default(false),
      file_name: z.string().nullable().optional(),
      page_name: z.string().nullable().optional(),
      selection_count: z.number().default(0),
    })
    .optional(),
});

export const ToolTraceItemSchema = z.object({
  tool: z.string(),
  action: z.string().optional().default(""),
  status: z.string().optional().default("OK"),
  duration_ms: z.number().optional(),
});

export const CreatedShapeSchema = z.object({
  shape_id: z.string(),
  name: z.string().optional().default("Shape"),
  type: z.string().optional().default("shape"),
});

export const SandboxArtifactSchema = z.object({
  path: z.string(),
  name: z.string(),
  tool: z.string().optional().default("sandbox"),
  download_url: z.string(),
});

export const AgentChatResponseSchema = z.object({
  status: z.string().default("OK"),
  reply: z.string().default(""),
  model: z.string().optional().default(""),
  latency_ms: z.number().optional(),
  tool_trace: z.array(ToolTraceItemSchema).optional().default([]),
  created_shapes: z.array(CreatedShapeSchema).optional().default([]),
  artifacts: z.array(SandboxArtifactSchema).optional().default([]),
  sandbox_persistent: z.boolean().optional().default(false),
});

export const PenpotUserRowSchema = z.object({
  id: z.string(),
  fullname: z.string(),
  email: z.string(),
  active_sessions: z.number().optional().default(0),
  has_ai_profile: z.boolean().optional().default(false),
});

export const PenpotUsersResponseSchema = z.object({
  status: z.string(),
  active_user: ActiveUserSchema.optional(),
  users: z.array(PenpotUserRowSchema).default([]),
});

export const PgvectorVisionResponseSchema = z.object({
  enabled: z.boolean().default(false),
  pgvector_active: z.boolean().default(false),
  engine: z.string().default(""),
  total_shapes: z.number().default(0),
  recommended_free_origin: z.object({ x: z.number(), y: z.number() }).optional(),
  prompt_summary: z.string().optional().default(""),
});

export const UploadedAttachmentSchema = z.object({
  id: z.string(),
  filename: z.string(),
  kind: z.string().default("file"),
  size_bytes: z.number().default(0),
  summary: z.string().default(""),
  imported_to_penpot: z.boolean().optional(),
  import_error: z.string().optional(),
});

export const UploadResponseSchema = z.object({
  status: z.string(),
  attachment: UploadedAttachmentSchema.optional(),
  error: z.string().optional(),
});

export type AgentStatus = z.infer<typeof AgentStatusSchema>;
export type AgentChatResponse = z.infer<typeof AgentChatResponseSchema>;
export type PenpotUserRow = z.infer<typeof PenpotUserRowSchema>;
export type PgvectorVisionResponse = z.infer<typeof PgvectorVisionResponseSchema>;
export type UploadedAttachment = z.infer<typeof UploadedAttachmentSchema>;
