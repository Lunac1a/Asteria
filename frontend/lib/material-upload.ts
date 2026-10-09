import type { Material } from "./workspace-model";

// Upload completion and document preparation are separate, real transport states.
export function uploadMaterial(workspaceId: string, file: File, uploaded: () => void): Promise<Material> {
  return new Promise((resolve,reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/workspaces/${encodeURIComponent(workspaceId)}/documents`);
    request.setRequestHeader("Authorization", `Bearer ${localStorage.getItem("access_token")}`);
    request.timeout = 120_000;
    request.upload.onload = uploaded;
    request.onerror = () => reject(new Error("Connection lost. Refresh the list before uploading again; your file may already have arrived."));
    request.ontimeout = () => reject(new Error("Preparation is taking longer than expected. Refresh the list to check your file before trying again."));
    request.onload = () => {
      if(request.status === 401) {
        localStorage.removeItem("access_token");
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign("/auth/login");
        reject(new Error("Please log in again.")); return;
      }
      let data;
      try { data=JSON.parse(request.responseText); } catch { reject(new Error("Could not confirm the upload. Refresh the list before trying again.")); return; }
      if(request.status >= 200 && request.status < 300) resolve(data as Material);
      else reject(new Error(typeof data.detail === "string" ? data.detail : "Upload failed. Please choose the file again."));
    };
    const body = new FormData(); body.append("file",file); request.send(body);
  });
}
