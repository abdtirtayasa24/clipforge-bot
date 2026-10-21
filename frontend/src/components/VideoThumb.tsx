import type { VideoOut } from "../api/types";
import { formatDuration } from "../format";
import { Icon } from "./Icon";

export function VideoThumb({ video }: { video: VideoOut }) {
  return (
    <span className="media-thumb wide">
      {video.thumbnail_url ? (
        <img src={video.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
      ) : (
        <Icon name="video" size={26} />
      )}
      {video.duration_seconds !== null && (
        <span className="duration">{formatDuration(video.duration_seconds * 1000)}</span>
      )}
    </span>
  );
}
