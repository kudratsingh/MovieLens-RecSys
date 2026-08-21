"use client";

import Image from "next/image";
import { useState } from "react";

export function MoviePoster({
  movieId,
  posterUrl,
  title,
  sizes,
  priority = false,
}: {
  movieId: number;
  posterUrl: string | null;
  title: string;
  sizes: string;
  priority?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const showImage = Boolean(posterUrl) && !failed;
  const hue = (movieId * 47) % 360;

  return (
    <div
      className="relative aspect-[2/3] overflow-hidden rounded-[1.15rem] bg-zinc-900"
      style={
        showImage
          ? undefined
          : {
              background: `radial-gradient(circle at 72% 20%, hsl(${hue} 48% 38% / .75), transparent 38%), linear-gradient(145deg, hsl(${hue} 30% 19%), #111214 72%)`,
            }
      }
    >
      {showImage && posterUrl ? (
        <Image
          alt={`${title} poster`}
          className="object-cover transition duration-500 group-hover:scale-[1.035]"
          fill
          onError={() => setFailed(true)}
          priority={priority}
          sizes={sizes}
          src={posterUrl}
        />
      ) : (
        <div className="absolute inset-0 flex items-end p-5">
          <span className="max-w-[13ch] text-xl font-semibold leading-tight text-white/90">
            {title}
          </span>
        </div>
      )}
      <div className="pointer-events-none absolute inset-0 ring-1 ring-inset ring-white/10" />
    </div>
  );
}
