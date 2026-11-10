import { request } from "@playwright/test";

/** Seed: the reviewer's worker analyses a few GigSim jobs (inline worker + scripted model). */
export default async function globalSetup() {
  const api = await request.newContext({ baseURL: "http://localhost:8100" });
  const login = await api.post("/api/auth/login", { data: { username: "reviewer", password: "reviewer-pass" } });
  const { token } = await login.json();
  const headers = { Authorization: `Bearer ${token}` };
  for (const job of ["job_00281", "job_00470", "job_00327", "job_00503"]) {
    const r = await api.post(`/api/jobs/${job}/analyse`, { headers });
    if (!r.ok()) throw new Error(`seeding ${job} failed: ${r.status()} ${await r.text()}`);
  }
  await api.dispose();
}
