// CameraPickerModal — small chooser shown before a Continuity Camera capture
// when more than one camera is available (e.g. paired iPhone + iPad + built-in
// FaceTime + a leftover virtual cam). Persists the user's choice to
// localStorage so subsequent scans skip the picker entirely; "Forget" clears
// that preference. Setting a non-Continuity camera as preferred also enables
// `--include-non-continuity` on the sidecar so the binary is allowed to use it.
//
// The shape of `cameras` matches what `list_continuity_cameras` returns on
// the Rust side (see `ContinuityCameraInfo` in src-tauri/src/lib.rs).

import React, { useEffect, useState } from 'react';
import { Modal } from './shared/Modal';
import { Smartphone, Camera, Monitor } from 'lucide-react';

export interface ContinuityCameraInfo {
  id: string;
  name: string;
  manufacturer: string;
  modelID: string;
  /** "continuity" | "external" | "builtin" | other */
  type: string;
  isContinuity: boolean;
}

export const CAMERA_PREF_STORAGE_KEY = 'localbook.continuityCamera.preferredId';

export function loadPreferredCameraId(): string | null {
  try { return localStorage.getItem(CAMERA_PREF_STORAGE_KEY); } catch { return null; }
}

export function savePreferredCameraId(id: string | null) {
  try {
    if (id) localStorage.setItem(CAMERA_PREF_STORAGE_KEY, id);
    else    localStorage.removeItem(CAMERA_PREF_STORAGE_KEY);
  } catch { /* ignore */ }
}

interface Props {
  isOpen: boolean;
  cameras: ContinuityCameraInfo[];
  /** Called with the chosen camera and whether to remember it. */
  onSelect: (cam: ContinuityCameraInfo, remember: boolean) => void;
  onCancel: () => void;
}

function iconFor(cam: ContinuityCameraInfo) {
  if (cam.isContinuity) return <Smartphone className="w-5 h-5 text-blue-500" />;
  if (cam.type === 'builtin') return <Monitor className="w-5 h-5 text-gray-500" />;
  return <Camera className="w-5 h-5 text-gray-500" />;
}

function subtitleFor(cam: ContinuityCameraInfo) {
  if (cam.isContinuity) return `iPhone / iPad — ${cam.manufacturer}`;
  if (cam.type === 'builtin')  return 'Built-in camera';
  if (cam.type === 'external') return `External — ${cam.manufacturer || 'unknown'}`;
  return cam.manufacturer || cam.type;
}

export const CameraPickerModal: React.FC<Props> = ({ isOpen, cameras, onSelect, onCancel }) => {
  // Pre-select the previously-remembered camera if it's still in the list,
  // otherwise the first Continuity device, otherwise the first device at all.
  const [selectedId, setSelectedId] = useState<string>('');
  const [remember, setRemember] = useState<boolean>(true);

  useEffect(() => {
    if (!isOpen || cameras.length === 0) return;
    const remembered = loadPreferredCameraId();
    const initial =
      cameras.find(c => c.id === remembered)?.id
      ?? cameras.find(c => c.isContinuity)?.id
      ?? cameras[0].id;
    setSelectedId(initial);
  }, [isOpen, cameras]);

  const confirm = () => {
    const cam = cameras.find(c => c.id === selectedId);
    if (cam) onSelect(cam, remember);
  };

  return (
    <Modal isOpen={isOpen} onClose={onCancel} title="Choose Camera" size="sm">
      <div className="p-4 space-y-3">
        {cameras.length === 0 ? (
          <p className="text-sm text-gray-600 dark:text-gray-300">
            No cameras found. Make sure your iPhone is unlocked and nearby, or
            connect a USB camera.
          </p>
        ) : (
          <>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Multiple cameras are available. Pick the one you want to scan with.
            </p>
            <div className="space-y-1.5">
              {cameras.map(cam => {
                const checked = cam.id === selectedId;
                return (
                  <label
                    key={cam.id}
                    className={`flex items-center gap-3 px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                      checked
                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                        : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                    }`}
                  >
                    <input
                      type="radio"
                      name="camera"
                      checked={checked}
                      onChange={() => setSelectedId(cam.id)}
                      className="shrink-0"
                    />
                    {iconFor(cam)}
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-gray-900 dark:text-white truncate">
                        {cam.name || 'Unnamed camera'}
                        {cam.isContinuity && (
                          <span className="ml-2 px-1.5 py-0.5 text-[10px] uppercase tracking-wide rounded bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                            iPhone
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
                        {subtitleFor(cam)}
                      </div>
                    </div>
                  </label>
                );
              })}
            </div>

            <label className="flex items-center gap-2 mt-2 text-xs text-gray-600 dark:text-gray-300">
              <input
                type="checkbox"
                checked={remember}
                onChange={e => setRemember(e.target.checked)}
              />
              Remember this choice
            </label>
          </>
        )}
      </div>

      <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-2">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={confirm}
          disabled={!selectedId}
          className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium transition-colors disabled:opacity-50"
        >
          Use this camera
        </button>
      </div>
    </Modal>
  );
};
